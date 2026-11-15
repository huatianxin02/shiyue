package local.shiyue.vault;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * A very small JSON reader/writer.
 *
 * The pack format is the contract between the Mac app and this one, so parsing it
 * must not depend on any library: a pack written by Python has to be readable here
 * years from now, on a phone, with nothing installed.
 */
final class Json {

    private Json() {}

    static Object parse(String text) {
        Reader r = new Reader(text);
        Object value = r.value();
        r.skipSpace();
        if (r.at < text.length()) throw r.error("内容结尾还有多余字符");
        return value;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asMap(Object value) {
        if (!(value instanceof Map)) throw new IllegalArgumentException("期望一个对象");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    static List<Object> asList(Object value) {
        if (!(value instanceof List)) throw new IllegalArgumentException("期望一个列表");
        return (List<Object>) value;
    }

    static String str(Map<String, Object> map, String key, String fallback) {
        Object v = map.get(key);
        return v instanceof String ? (String) v : fallback;
    }

    static int integer(Map<String, Object> map, String key, int fallback) {
        Object v = map.get(key);
        return v instanceof Number ? ((Number) v).intValue() : fallback;
    }

    /** Canonical form: keys sorted, no whitespace, UTF-8 left unescaped. */
    static String write(Object value) {
        StringBuilder out = new StringBuilder(256);
        write(out, value);
        return out.toString();
    }

    private static void write(StringBuilder out, Object value) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String) {
            quote(out, (String) value);
        } else if (value instanceof Boolean) {
            out.append(((Boolean) value).booleanValue() ? "true" : "false");
        } else if (value instanceof Number) {
            number(out, (Number) value);
        } else if (value instanceof Map) {
            out.append('{');
            Map<String, Object> sorted = new TreeMap<String, Object>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) value).entrySet()) {
                sorted.put(String.valueOf(e.getKey()), e.getValue());
            }
            boolean first = true;
            for (Map.Entry<String, Object> e : sorted.entrySet()) {
                if (!first) out.append(',');
                first = false;
                quote(out, e.getKey());
                out.append(':');
                write(out, e.getValue());
            }
            out.append('}');
        } else if (value instanceof List) {
            out.append('[');
            List<?> items = (List<?>) value;
            for (int i = 0; i < items.size(); i++) {
                if (i > 0) out.append(',');
                write(out, items.get(i));
            }
            out.append(']');
        } else {
            quote(out, String.valueOf(value));
        }
    }

    private static void number(StringBuilder out, Number n) {
        if (n instanceof Double || n instanceof Float) {
            double d = n.doubleValue();
            if (d == Math.rint(d) && !Double.isInfinite(d)) out.append((long) d);
            else out.append(d);
        } else {
            out.append(n.toString());
        }
    }

    private static void quote(StringBuilder out, String s) {
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
                    else out.append(c);
            }
        }
        out.append('"');
    }

    private static final class Reader {
        private final String text;
        private int at;

        Reader(String text) { this.text = text; }

        IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + "（位置 " + at + "）");
        }

        void skipSpace() {
            while (at < text.length()) {
                char c = text.charAt(at);
                if (c == ' ' || c == '\n' || c == '\r' || c == '\t') at++;
                else break;
            }
        }

        Object value() {
            skipSpace();
            if (at >= text.length()) throw error("内容意外结束");
            char c = text.charAt(at);
            switch (c) {
                case '{': return object();
                case '[': return array();
                case '"': return string();
                case 't': literal("true"); return Boolean.TRUE;
                case 'f': literal("false"); return Boolean.FALSE;
                case 'n': literal("null"); return null;
                default: return number();
            }
        }

        private void literal(String word) {
            if (!text.startsWith(word, at)) throw error("无法识别的内容");
            at += word.length();
        }

        private Map<String, Object> object() {
            Map<String, Object> map = new LinkedHashMap<String, Object>();
            at++;
            skipSpace();
            if (at < text.length() && text.charAt(at) == '}') { at++; return map; }
            while (true) {
                skipSpace();
                if (at >= text.length() || text.charAt(at) != '"') throw error("对象的键必须是字符串");
                String key = string();
                skipSpace();
                if (at >= text.length() || text.charAt(at) != ':') throw error("键后面缺少冒号");
                at++;
                map.put(key, value());
                skipSpace();
                if (at >= text.length()) throw error("对象没有结束");
                char c = text.charAt(at++);
                if (c == '}') return map;
                if (c != ',') throw error("对象成员之间缺少逗号");
            }
        }

        private List<Object> array() {
            List<Object> list = new ArrayList<Object>();
            at++;
            skipSpace();
            if (at < text.length() && text.charAt(at) == ']') { at++; return list; }
            while (true) {
                list.add(value());
                skipSpace();
                if (at >= text.length()) throw error("列表没有结束");
                char c = text.charAt(at++);
                if (c == ']') return list;
                if (c != ',') throw error("列表项之间缺少逗号");
            }
        }

        private String string() {
            at++;
            StringBuilder out = new StringBuilder();
            while (true) {
                if (at >= text.length()) throw error("字符串没有结束");
                char c = text.charAt(at++);
                if (c == '"') return out.toString();
                if (c != '\\') { out.append(c); continue; }
                if (at >= text.length()) throw error("转义不完整");
                char e = text.charAt(at++);
                switch (e) {
                    case '"': out.append('"'); break;
                    case '\\': out.append('\\'); break;
                    case '/': out.append('/'); break;
                    case 'b': out.append('\b'); break;
                    case 'f': out.append('\f'); break;
                    case 'n': out.append('\n'); break;
                    case 'r': out.append('\r'); break;
                    case 't': out.append('\t'); break;
                    case 'u':
                        if (at + 4 > text.length()) throw error("Unicode 转义不完整");
                        out.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                        break;
                    default: throw error("无法识别的转义");
                }
            }
        }

        private Object number() {
            int start = at;
            if (at < text.length() && (text.charAt(at) == '-' || text.charAt(at) == '+')) at++;
            boolean fraction = false;
            while (at < text.length()) {
                char c = text.charAt(at);
                if (c >= '0' && c <= '9') { at++; continue; }
                if (c == '.' || c == 'e' || c == 'E' || c == '-' || c == '+') { fraction = fraction || c == '.' || c == 'e' || c == 'E'; at++; continue; }
                break;
            }
            String raw = text.substring(start, at);
            if (raw.isEmpty()) throw error("无法识别的数值");
            try {
                if (fraction) return Double.valueOf(raw);
                return Long.valueOf(raw);
            } catch (NumberFormatException e) {
                throw error("数值格式错误");
            }
        }
    }
}
