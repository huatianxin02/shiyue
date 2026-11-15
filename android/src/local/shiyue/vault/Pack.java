package local.shiyue.vault;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.Charset;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

/**
 * Reads and writes 拾页 packs (.vaultpack).
 *
 * The Mac app is the authority on this format; this class repeats its checks rather
 * than trusting the archive. Nothing is registered until every object has matched
 * its SHA-256, because a half-verified pack is worse than none: the reader would
 * believe they are looking at the file that was packed.
 *
 * No Android types appear here, so the whole path runs on a desktop JVM too.
 */
final class Pack {

    static final String FORMAT = "shiyue-vaultpack";
    static final int SCHEMA_VERSION = 1;
    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final Pattern OBJECT = Pattern.compile("objects/[0-9a-f]{64}");
    private static final Pattern FILE_ID = Pattern.compile("[0-9a-f]{32}");
    private static final Pattern SHA = Pattern.compile("[0-9a-f]{64}");
    private static final long MAX_MANIFEST = 64L * 1000 * 1000;
    private static final int MAX_ENTRIES = 1000003;
    /** Objects at or below this size are verified in memory; larger ones stream. */
    private static final long IN_MEMORY_LIMIT = 8L * 1024 * 1024;

    private Pack() {}

    /** Where verified bytes end up. Implemented by the phone library and by tests. */
    interface Store {
        File objectPath(String sha);
        boolean hasObject(String sha);
        List<Record> all();
        void put(Record record);
        default void beginImport() {}
        default void commitImport() {}
        default void rollbackImport() {}
    }

    interface Progress {
        void step(int done, int total, String label);
        boolean cancelled();
    }

    static final class Record {
        String id;
        String sha;
        String name;
        String kind = "document";
        String tags = "";
        String addedAt = "";
        String source = "";
        int starred;
        long size;
        List<String> shelves = new ArrayList<String>();

        Map<String, Object> toJson() {
            Map<String, Object> map = new LinkedHashMap<String, Object>();
            map.put("id", id);
            map.put("sha", sha);
            map.put("name", name);
            map.put("kind", kind);
            map.put("tags", splitTags(tags));
            map.put("starred", Integer.valueOf(starred));
            map.put("added_at", addedAt);
            map.put("source_name", source);
            map.put("collections", new ArrayList<String>(shelves));
            return map;
        }
    }

    static final class Summary {
        int files;
        int objects;
        int skipped;
        long bytes;
        String packageId = "";
        final Set<String> shelves = new LinkedHashSet<String>();
    }

    // ---------------------------------------------------------------- import

    static Summary importPack(File archive, Store store, Progress progress) throws IOException {
        Summary summary = new Summary();
        ZipFile zip = new ZipFile(archive);
        try {
            Map<String, ZipEntry> entries = new LinkedHashMap<String, ZipEntry>();
            Enumeration<? extends ZipEntry> walk = zip.entries();
            while (walk.hasMoreElements()) {
                ZipEntry entry = walk.nextElement();
                String name = entry.getName();
                if (!name.equals("manifest.json") && !name.equals("checksums.json")
                        && !name.equals("root.sha256") && !OBJECT.matcher(name).matches()) {
                    throw new IOException("包内包含无法识别的条目：" + name);
                }
                if (entries.put(name, entry) != null) throw new IOException("资料包存在重复条目");
            }
            if (entries.size() > MAX_ENTRIES) throw new IOException("资料包条目过多");
            for (ZipEntry entry : entries.values()) {
                if (entry.getSize() > Math.max(entry.getCompressedSize(), 1L) * 1000) {
                    throw new IOException("包内压缩比异常：" + entry.getName());
                }
            }
            ZipEntry manifestEntry = entries.get("manifest.json");
            ZipEntry checksEntry = entries.get("checksums.json");
            ZipEntry rootEntry = entries.get("root.sha256");
            if (manifestEntry == null || checksEntry == null || rootEntry == null) {
                throw new IOException("这不是拾页资料包：缺少清单文件");
            }
            if (manifestEntry.getSize() > MAX_MANIFEST || checksEntry.getSize() > MAX_MANIFEST) {
                throw new IOException("资料包清单过大");
            }

            byte[] checksBytes = read(zip, checksEntry);
            String rootText = new String(read(zip, rootEntry), UTF8).trim();
            if (!sha256(checksBytes).equals(rootText)) throw new IOException("根校验失败，资料包可能已损坏");

            Map<String, Object> checks = Json.asMap(Json.parse(new String(checksBytes, UTF8)));
            Set<String> expected = new LinkedHashSet<String>(entries.keySet());
            expected.remove("checksums.json");
            expected.remove("root.sha256");
            if (!checks.keySet().equals(expected)) throw new IOException("包校验清单不完整");

            byte[] manifestBytes = read(zip, manifestEntry);
            Object manifestSha = checks.get("manifest.json");
            if (!(manifestSha instanceof String) || !sha256(manifestBytes).equals(manifestSha)) {
                throw new IOException("清单校验失败");
            }

            Map<String, Object> manifest = Json.asMap(Json.parse(new String(manifestBytes, UTF8)));
            if (!FORMAT.equals(Json.str(manifest, "format", ""))) throw new IOException("不是拾页资料包");
            if (Json.integer(manifest, "schema_version", 0) != SCHEMA_VERSION) {
                throw new IOException("资料包版本不受支持，请升级应用");
            }
            if (!"full".equals(Json.str(manifest, "kind", ""))) throw new IOException("不支持此类型的资料包");
            summary.packageId = Json.str(manifest, "package_id", "");

            List<Record> records = new ArrayList<Record>();
            Set<String> recordIds = new LinkedHashSet<String>();
            Set<String> shas = new LinkedHashSet<String>();
            Map<String, Long> sizes = new LinkedHashMap<String, Long>();
            for (Object raw : Json.asList(manifest.get("files"))) {
                Map<String, Object> item = Json.asMap(raw);
                Record record = new Record();
                record.id = Json.str(item, "id", "");
                record.sha = Json.str(item, "sha", "");
                record.name = Json.str(item, "name", "");
                record.kind = Json.str(item, "kind", "document");
                record.addedAt = Json.str(item, "added_at", "");
                record.source = Json.str(item, "source_name", "");
                record.starred = Json.integer(item, "starred", 0);

                if (!FILE_ID.matcher(record.id).matches()) throw new IOException("文件记录 ID 无效");
                if (!recordIds.add(record.id)) throw new IOException("文件记录 ID 重复");
                if (!SHA.matcher(record.sha).matches()) throw new IOException("对象校验记录无效");
                if (!record.sha.equals(checks.get("objects/" + record.sha))) {
                    throw new IOException("对象校验记录不一致");
                }
                if (record.name.isEmpty() || record.name.length() > 1000
                        || record.name.indexOf('/') >= 0 || record.name.indexOf('\\') >= 0) {
                    throw new IOException("文件名不合法");
                }
                record.tags = joinTags(item.get("tags"));
                record.shelves.addAll(readShelves(item.get("collections")));
                for (String shelf : record.shelves) summary.shelves.add(shelf);

                ZipEntry object = entries.get("objects/" + record.sha);
                if (object == null) throw new IOException("资料包缺少文件内容：" + record.name);
                record.size = object.getSize();
                sizes.put(record.sha, Long.valueOf(record.size));
                records.add(record);
                shas.add(record.sha);
            }
            if (records.isEmpty()) throw new IOException("资料包中没有文件");

            Set<String> presentObjects = new LinkedHashSet<String>();
            for (String name : checks.keySet()) if (name.startsWith("objects/")) presentObjects.add(name);
            Set<String> neededObjects = new LinkedHashSet<String>();
            for (String sha : shas) neededObjects.add("objects/" + sha);
            if (!presentObjects.equals(neededObjects)) throw new IOException("包内对象集合不一致");

            summary.files = records.size();
            summary.objects = shas.size();
            List<String> ordered = new ArrayList<String>(shas);
            Collections.sort(ordered);
            int done = 0;
            for (String sha : ordered) {
                if (progress != null && progress.cancelled()) throw new CancelledException();
                ZipEntry entry = entries.get("objects/" + sha);
                File target = store.objectPath(sha);
                long size = entry.getSize();
                summary.bytes += size;
                if (store.hasObject(sha) && target.length() == size && sha.equals(hashFile(target))) {
                    verifyEntry(zip, entry, sha);
                    summary.skipped++;
                } else {
                    extractVerified(zip, entry, target, sha);
                }
                done++;
                if (progress != null) progress.step(done, ordered.size(), "校验 " + nameOf(records, sha));
            }

            store.beginImport();
            try {
                for (Record record : records) {
                    if (progress != null && progress.cancelled()) throw new CancelledException();
                    store.put(record);
                }
                store.commitImport();
            } catch (RuntimeException | IOException error) {
                store.rollbackImport();
                throw error;
            }
            return summary;
        } finally {
            zip.close();
        }
    }

    private static String hashFile(File file) throws IOException {
        MessageDigest digest = newSha256();
        try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[65536]; int n;
            while ((n = in.read(buffer)) > 0) digest.update(buffer, 0, n);
        }
        return hex(digest.digest());
    }
    private static void verifyEntry(ZipFile zip, ZipEntry entry, String expected) throws IOException {
        MessageDigest digest = newSha256();
        try (InputStream in = zip.getInputStream(entry)) {
            byte[] buffer = new byte[65536]; int n;
            while ((n = in.read(buffer)) > 0) digest.update(buffer, 0, n);
        }
        if (!expected.equals(hex(digest.digest()))) throw new IOException("包内对象校验失败");
    }

    private static String joinTags(Object tags) throws IOException {
        if (tags == null) return "";
        List<Object> list = Json.asList(tags);
        if (list.size() > 50) throw new IOException("标签过多");
        StringBuilder text = new StringBuilder();
        for (Object tag : list) {
            if (!(tag instanceof String) || ((String) tag).length() > 80) throw new IOException("标签格式不合法");
            if (text.length() > 0) text.append('\u0001');
            text.append((String) tag);
        }
        return text.toString();
    }

    private static List<String> readShelves(Object shelves) throws IOException {
        List<String> out = new ArrayList<String>();
        if (shelves == null) return out;
        List<Object> list = Json.asList(shelves);
        if (list.size() > 20) throw new IOException("分类过多");
        for (Object name : list) {
            if (!(name instanceof String)) throw new IOException("分类格式不合法");
            String clean = ((String) name).trim();
            if (clean.isEmpty() || clean.length() > 40) throw new IOException("分类格式不合法");
            if (!out.contains(clean)) out.add(clean);
        }
        return out;
    }

    private static String nameOf(List<Record> records, String sha) {
        for (Record record : records) if (record.sha.equals(sha)) return record.name;
        return sha.substring(0, 8);
    }

    private static void extractVerified(ZipFile zip, ZipEntry entry, File target, String sha) throws IOException {
        File parent = target.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) throw new IOException("无法创建存储目录");
        File temp = new File(parent, ".incoming-" + sha.substring(0, 12));
        MessageDigest digest = newSha256();
        InputStream in = zip.getInputStream(entry);
        try {
            OutputStream out = new FileOutputStream(temp);
            try {
                byte[] buffer = new byte[64 * 1024];
                int read;
                while ((read = in.read(buffer)) > 0) {
                    digest.update(buffer, 0, read);
                    out.write(buffer, 0, read);
                }
            } finally {
                out.close();
            }
        } finally {
            in.close();
        }
        if (!hex(digest.digest()).equals(sha)) {
            temp.delete();
            throw new IOException("对象校验失败：" + sha.substring(0, 12) + "，资料包可能被改动");
        }
        try {
            java.nio.file.Files.move(temp.toPath(),target.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING,java.nio.file.StandardCopyOption.ATOMIC_MOVE);
        } catch(IOException failure) {
            temp.delete();throw new IOException("无法原子保存文件，已有内容未被主动删除",failure);
        }
    }

    // ---------------------------------------------------------------- export

    /**
     * Writes a pack the Mac app can mount: same member names, same manifest shape,
     * same root-checksum chain. Collections travel with it, so a shelf made on the
     * phone is still a shelf after the round trip.
     */
    static Summary exportPack(File destination, Store store, Progress progress) throws IOException {
        List<Record> records = store.all();
        if (records.isEmpty()) throw new IOException("资料库为空，没有可以导出的内容");
        Summary summary = new Summary();
        summary.files = records.size();

        Set<String> shas = new LinkedHashSet<String>();
        for (Record record : records) shas.add(record.sha);
        List<String> ordered = new ArrayList<String>(shas);
        Collections.sort(ordered);
        summary.objects = ordered.size();

        Map<String, String> checks = new LinkedHashMap<String, String>();
        File temp = new File(destination.getParentFile(), destination.getName() + ".part");
        if (temp.exists() && !temp.delete()) throw new IOException("无法写入导出目录");
        ZipOutputStream zip = new ZipOutputStream(new FileOutputStream(temp));
        zip.setMethod(ZipOutputStream.STORED);
        try {
            int done = 0;
            for (String sha : ordered) {
                if (progress != null && progress.cancelled()) throw new CancelledException();
                File source = store.objectPath(sha);
                if (!source.isFile()) throw new IOException("资料库中缺少文件内容：" + sha.substring(0, 12));
                writeObject(zip, source, sha);
                checks.put("objects/" + sha, sha);
                done++;
                if (progress != null) progress.step(done, ordered.size(), "打包 " + nameOf(records, sha));
            }

            List<Object> files = new ArrayList<Object>();
            for (Record record : records) {
                files.add(record.toJson());
                for (String shelf : record.shelves) summary.shelves.add(shelf);
            }
            Map<String, Object> manifest = new LinkedHashMap<String, Object>();
            manifest.put("format", FORMAT);
            manifest.put("schema_version", Integer.valueOf(SCHEMA_VERSION));
            manifest.put("kind", "full");
            manifest.put("package_id", java.util.UUID.randomUUID().toString().replace("-", ""));
            manifest.put("created_at", new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", java.util.Locale.US)
                    .format(new java.util.Date()));
            manifest.put("files", files);
            manifest.put("objects", Integer.valueOf(ordered.size()));
            byte[] manifestBytes = Json.write(manifest).getBytes(UTF8);
            writeStored(zip, "manifest.json", manifestBytes);
            checks.put("manifest.json", sha256(manifestBytes));

            byte[] checksBytes = Json.write(checks).getBytes(UTF8);
            writeStored(zip, "checksums.json", checksBytes);
            writeStored(zip, "root.sha256", sha256(checksBytes).getBytes(UTF8));
        } finally {
            zip.close();
        }
        if (destination.exists() && !destination.delete()) throw new IOException("无法覆盖同名文件");
        if (!temp.renameTo(destination)) {
            temp.delete();
            throw new IOException("无法保存资料包");
        }
        summary.bytes = destination.length();
        return summary;
    }

    private static void writeObject(ZipOutputStream zip, File source, String sha) throws IOException {
        long length = source.length();
        if (length <= IN_MEMORY_LIMIT) {
            byte[] data = readFile(source);
            if (!sha256(data).equals(sha)) throw new IOException("内容已变化，导出已取消：" + sha.substring(0, 12));
            writeStored(zip, "objects/" + sha, data);
            return;
        }
        // Large objects (video) are streamed; the CRC must be known before the header.
        CRC32 crc = new CRC32();
        MessageDigest digest = newSha256();
        InputStream probe = new FileInputStream(source);
        try {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = probe.read(buffer)) > 0) {
                digest.update(buffer, 0, read);
                crc.update(buffer, 0, read);
            }
        } finally {
            probe.close();
        }
        if (!hex(digest.digest()).equals(sha)) throw new IOException("内容已变化，导出已取消：" + sha.substring(0, 12));
        ZipEntry entry = new ZipEntry("objects/" + sha);
        entry.setMethod(ZipEntry.STORED);
        entry.setSize(length);
        entry.setCompressedSize(length);
        entry.setCrc(crc.getValue());
        zip.putNextEntry(entry);
        InputStream body = new FileInputStream(source);
        try {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = body.read(buffer)) > 0) zip.write(buffer, 0, read);
        } finally {
            body.close();
        }
        zip.closeEntry();
    }

    private static void writeStored(ZipOutputStream zip, String name, byte[] data) throws IOException {
        ZipEntry entry = new ZipEntry(name);
        entry.setMethod(ZipEntry.STORED);
        entry.setSize(data.length);
        entry.setCompressedSize(data.length);
        CRC32 crc = new CRC32();
        crc.update(data, 0, data.length);
        entry.setCrc(crc.getValue());
        zip.putNextEntry(entry);
        zip.write(data);
        zip.closeEntry();
    }

    // --------------------------------------------------------------- helpers

    static final class CancelledException extends IOException {
        CancelledException() { super("已取消"); }
    }

    private static List<Object> splitTags(String tags) {
        List<Object> out = new ArrayList<Object>();
        if (tags == null || tags.isEmpty()) return out;
        for (String part : tags.split("\u0001")) if (!part.isEmpty()) out.add(part);
        return out;
    }

    private static byte[] readFile(File file) throws IOException {
        long length = file.length();
        if (length > Integer.MAX_VALUE) throw new IOException("文件过大");
        ByteArrayOutputStream out = new ByteArrayOutputStream((int) Math.max(length, 8192));
        InputStream in = new FileInputStream(file);
        try {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
        } finally {
            in.close();
        }
        return out.toByteArray();
    }

    private static byte[] read(ZipFile zip, ZipEntry entry) throws IOException {
        long size = entry.getSize();
        ByteArrayOutputStream out = new ByteArrayOutputStream(size > 0 ? (int) size : 8192);
        InputStream in = zip.getInputStream(entry);
        try {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
        } finally {
            in.close();
        }
        return out.toByteArray();
    }

    private static MessageDigest newSha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (Exception e) {
            throw new IllegalStateException("缺少 SHA-256 支持", e);
        }
    }

    static String sha256(byte[] data) {
        return hex(newSha256().digest(data));
    }

    static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) {
            out.append(Character.forDigit((b >> 4) & 0xf, 16));
            out.append(Character.forDigit(b & 0xf, 16));
        }
        return out.toString();
    }
}
