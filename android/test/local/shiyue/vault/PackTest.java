package local.shiyue.vault;

import java.io.File;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Exercises the pack path on a desktop JVM: import a real Mac export, export it
 * again, then import that second pack. Runs without a phone or an emulator.
 */
public final class PackTest {

    static int failures = 0;

    public static void main(String[] args) throws Exception {
        File pack = new File(args[0]);
        File work = new File(args[1]);
        deleteTree(work);
        work.mkdirs();

        DirStore first = new DirStore(new File(work, "a"));
        Pack.Summary imported = Pack.importPack(pack, first, quiet());
        System.out.println("import: files=" + imported.files + " objects=" + imported.objects
                + " bytes=" + imported.bytes + " skipped=" + imported.skipped
                + " shelves=" + imported.shelves);
        check("文件数", imported.files == 4, String.valueOf(imported.files));
        check("对象数", imported.objects == 4, String.valueOf(imported.objects));
        check("分类数", imported.shelves.size() == 2, imported.shelves.toString());
        check("记录入库", first.records.size() == 4, String.valueOf(first.records.size()));
        for (Pack.Record r : first.records.values()) {
            check("对象落盘 " + r.name, first.objectPath(r.sha).isFile(), "");
            check("大小有效 " + r.name, r.size > 0, String.valueOf(r.size));
        }
        Pack.Record image = findByKind(first, "image");
        check("识别图片", image != null, "null");
        check("图片分类", image != null && image.shelves.contains("AI资料"), image == null ? "" : image.shelves.toString());

        File exported = new File(work, "roundtrip.vaultpack");
        Pack.Summary out = Pack.exportPack(exported, first, quiet());
        System.out.println("export: files=" + out.files + " objects=" + out.objects + " bytes=" + out.bytes);
        check("导出文件存在", exported.isFile() && exported.length() > 0, "");
        check("导出文件数", out.files == 4, String.valueOf(out.files));

        DirStore second = new DirStore(new File(work, "b"));
        Pack.Summary again = Pack.importPack(exported, second, quiet());
        check("二次导入文件数", again.files == 4, String.valueOf(again.files));
        check("二次导入分类", again.shelves.equals(imported.shelves), again.shelves.toString());
        for (Pack.Record a : first.records.values()) {
            Pack.Record b = second.records.get(a.id);
            check("往返一致 " + a.name, b != null && b.sha.equals(a.sha) && b.name.equals(a.name)
                    && b.kind.equals(a.kind) && b.shelves.containsAll(a.shelves), b == null ? "missing" : b.name);
        }

        DirStore third = new DirStore(new File(work, "c"));
        Pack.importPack(pack, third, quiet());
        Pack.Summary repeat = Pack.importPack(pack, third, quiet());
        check("重复导入跳过已有对象", repeat.skipped == repeat.objects, repeat.skipped + "/" + repeat.objects);
        check("重复导入不产生副本", third.records.size() == 4, String.valueOf(third.records.size()));

        File broken = new File(work, "broken.vaultpack");
        copy(pack, broken);
        corrupt(broken);
        boolean rejected = false;
        String reason = "";
        try {
            Pack.importPack(broken, new DirStore(new File(work, "d")), quiet());
        } catch (Exception e) {
            rejected = true;
            reason = e.getMessage();
        }
        check("损坏包被拒绝", rejected, reason);

        System.out.println(failures == 0 ? "ALL PASS" : failures + " FAILURES");
        if (failures > 0) System.exit(1);
    }

    private static Pack.Record findByKind(DirStore store, String kind) {
        for (Pack.Record r : store.records.values()) if (kind.equals(r.kind)) return r;
        return null;
    }

    private static Pack.Progress quiet() {
        return new Pack.Progress() {
            public void step(int done, int total, String label) {}
            public boolean cancelled() { return false; }
        };
    }

    private static void check(String label, boolean ok, String detail) {
        if (!ok) {
            failures++;
            System.out.println("FAIL " + label + " -> " + detail);
        }
    }

    private static void copy(File from, File to) throws Exception {
        java.io.InputStream in = new java.io.FileInputStream(from);
        java.io.OutputStream out = new java.io.FileOutputStream(to);
        try {
            byte[] buffer = new byte[65536];
            int read;
            while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
        } finally {
            in.close();
            out.close();
        }
    }

    /** Flips one byte inside the archive so a checksum must fail. */
    private static void corrupt(File file) throws Exception {
        java.io.RandomAccessFile raf = new java.io.RandomAccessFile(file, "rw");
        try {
            long at = file.length() / 2;
            raf.seek(at);
            int original = raf.read();
            raf.seek(at);
            raf.write(original ^ 0xff);
        } finally {
            raf.close();
        }
    }

    private static void deleteTree(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteTree(child);
        file.delete();
    }

    static final class DirStore implements Pack.Store {
        final File root;
        final Map<String, Pack.Record> records = new LinkedHashMap<String, Pack.Record>();

        DirStore(File root) {
            this.root = root;
            root.mkdirs();
        }

        public File objectPath(String sha) {
            return new File(new File(root, sha.substring(0, 2)), sha);
        }

        public boolean hasObject(String sha) {
            return objectPath(sha).isFile();
        }

        public List<Pack.Record> all() {
            return new ArrayList<Pack.Record>(records.values());
        }

        public void put(Pack.Record record) {
            records.put(record.id, record);
        }
    }
}
