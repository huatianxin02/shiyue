package local.shiyue.vault;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.Charset;
import java.nio.charset.CodingErrorAction;
import java.util.Locale;

/**
 * Reading and fitting HTML that was written for a desktop column.
 *
 * These notes are 920 px wide, so on a 412 dp screen they run off the edge — in our
 * own WebView and in the browser alike. A viewport plus a stylesheet that lets wide
 * blocks shrink or scroll inside themselves fixes both.
 */
final class Html {

    private Html() {}

    private static final String HEAD =
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            + "<style>"
            + "html,body{width:auto!important;max-width:100%!important;overflow-x:hidden!important}"
            + "body{margin-left:0!important;margin-right:0!important}"
            + "div,section,main,article,header,footer,aside,figure{max-width:100%!important}"
            + "img,svg,video,canvas,iframe{max-width:100%!important;height:auto!important}"
            + "table{max-width:100%!important;display:block;overflow-x:auto}"
            + "pre,code{max-width:100%!important;white-space:pre-wrap!important;word-break:break-word!important}"
            + "p,li,td,th,h1,h2,h3,h4{word-wrap:break-word!important;overflow-wrap:break-word!important}"
            + "</style>";

    static String fit(String html) {
        if (html == null) return null;
        String lower = html.toLowerCase(Locale.ROOT);
        int close = lower.indexOf("</head>");
        if (close >= 0) return html.substring(0, close) + HEAD + html.substring(close);
        int open = lower.indexOf("<body");
        if (open >= 0) return html.substring(0, open) + HEAD + html.substring(open);
        return HEAD + html;
    }

    /** UTF-8 first, then GB18030 for text saved on older Chinese systems. */
    static String decode(byte[] bytes) {
        String text = decodeStrict(bytes, "UTF-8");
        if (text == null) text = decodeStrict(bytes, "GB18030");
        if (text == null) text = new String(bytes, Charset.forName("UTF-8"));
        return text;
    }

    private static String decodeStrict(byte[] bytes, String charset) {
        try {
            return Charset.forName(charset).newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes)).toString();
        } catch (CharacterCodingException e) {
            return null;
        } catch (Exception e) {
            return null;
        }
    }

    /** Returns null when the file is binary or larger than the limit. */
    static byte[] read(File source, int limit) {
        try {
            InputStream in = new FileInputStream(source);
            try {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buffer = new byte[32 * 1024];
                int read;
                while ((read = in.read(buffer)) > 0) {
                    if (out.size() + read > limit) return null;
                    out.write(buffer, 0, read);
                }
                byte[] bytes = out.toByteArray();
                for (byte b : bytes) if (b == 0) return null;
                return bytes;
            } finally {
                in.close();
            }
        } catch (Exception e) {
            return null;
        }
    }
}
