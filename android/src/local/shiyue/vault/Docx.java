package local.shiyue.vault;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Reads the words out of a .docx.
 *
 * A .docx is a zip whose `word/document.xml` holds the text in `w:t` runs, one
 * paragraph per `w:p`. Extracting those runs gives readable content with no
 * dependency and no network, which is the whole point: a packed document should
 * still be readable on a phone that has no office suite installed.
 *
 * Formatting is lost on purpose. Layout is the office app's job; this is only a
 * safety net so the file is never a dead end.
 */
final class Docx {

    private static final String DOCUMENT = "word/document.xml";

    private Docx() {}

    static boolean isDocx(String name) {
        String lower = name == null ? "" : name.toLowerCase(java.util.Locale.ROOT);
        return lower.endsWith(".docx");
    }

    /** Plain text of the document, or null if the file is not a readable .docx. */
    static String text(File file, int limit) {
        if (file == null || !file.isFile()) return null;
        ZipInputStream zip = null;
        try {
            zip = new ZipInputStream(new BufferedInputStream(new FileInputStream(file)));
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                if (!DOCUMENT.equals(entry.getName())) continue;
                String xml = readEntry(zip, 16 * 1024 * 1024);
                if (xml == null) return null;
                String text = extract(xml, limit);
                return text.trim().isEmpty() ? null : text;
            }
            return null;
        } catch (Exception e) {
            return null;
        } finally {
            if (zip != null) {
                try {
                    zip.close();
                } catch (Exception ignored) {
                }
            }
        }
    }

    private static String readEntry(InputStream in, int limit) throws Exception {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream(64 * 1024);
        byte[] chunk = new byte[32 * 1024];
        int read;
        while ((read = in.read(chunk)) > 0) {
            buffer.write(chunk, 0, read);
            if (buffer.size() > limit) return null;
        }
        return buffer.toString("UTF-8");
    }

    /**
     * Walks the XML tag by tag. Only text runs matter, plus the few structural
     * tags that become whitespace: paragraph end, line break, tab, table cell.
     */
    private static String extract(String xml, int limit) {
        StringBuilder out = new StringBuilder(Math.min(xml.length() / 4, limit));
        int index = 0;
        while (index < xml.length() && out.length() < limit) {
            int open = xml.indexOf('<', index);
            if (open < 0) break;
            int close = xml.indexOf('>', open);
            if (close < 0) break;
            String tag = xml.substring(open + 1, close);
            index = close + 1;

            if (tag.equals("w:t") || tag.startsWith("w:t ")) {
                int end = xml.indexOf("</w:t>", index);
                if (end < 0) break;
                out.append(unescape(xml.substring(index, end)));
                index = end + "</w:t>".length();
            } else if (tag.equals("w:tab/") || tag.equals("w:tab")) {
                out.append('\t');
            } else if (tag.startsWith("w:br") && !tag.startsWith("w:brk")) {
                out.append('\n');
            } else if (tag.equals("/w:p")) {
                out.append('\n');
            } else if (tag.equals("/w:tc")) {
                out.append('\t');
            } else if (tag.equals("/w:tr")) {
                out.append('\n');
            }
        }
        return tidy(out.toString());
    }

    /** Collapses the runs of blank paragraphs Word leaves between blocks. */
    private static String tidy(String raw) {
        String[] lines = raw.replace("\r\n", "\n").replace('\r', '\n').split("\n", -1);
        StringBuilder out = new StringBuilder(raw.length());
        boolean blank = true;
        for (String line : lines) {
            String trimmed = line.replaceAll("[ \t]+$", "");
            if (trimmed.trim().isEmpty()) {
                if (blank) continue;
                blank = true;
                out.append('\n');
            } else {
                blank = false;
                out.append(trimmed).append('\n');
            }
        }
        return out.toString().trim();
    }

    private static String unescape(String value) {
        if (value.indexOf('&') < 0) return value;
        StringBuilder out = new StringBuilder(value.length());
        int index = 0;
        while (index < value.length()) {
            char c = value.charAt(index);
            if (c != '&') {
                out.append(c);
                index++;
                continue;
            }
            int semi = value.indexOf(';', index);
            if (semi < 0 || semi - index > 10) {
                out.append(c);
                index++;
                continue;
            }
            String entity = value.substring(index + 1, semi);
            if (entity.equals("amp")) out.append('&');
            else if (entity.equals("lt")) out.append('<');
            else if (entity.equals("gt")) out.append('>');
            else if (entity.equals("quot")) out.append('"');
            else if (entity.equals("apos")) out.append('\'');
            else if (entity.startsWith("#")) {
                try {
                    int code = entity.startsWith("#x") || entity.startsWith("#X")
                            ? Integer.parseInt(entity.substring(2), 16)
                            : Integer.parseInt(entity.substring(1));
                    out.appendCodePoint(code);
                } catch (Exception e) {
                    out.append('&').append(entity).append(';');
                }
            } else {
                out.append('&').append(entity).append(';');
            }
            index = semi + 1;
        }
        return out.toString();
    }
}
