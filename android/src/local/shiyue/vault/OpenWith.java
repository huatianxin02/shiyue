package local.shiyue.vault;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ContentValues;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.widget.Toast;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Locale;

/**
 * How a file leaves the reading view.
 *
 * Some formats simply read better elsewhere: a wide HTML page in the browser, a
 * .docx in the office app the user already has. The library is content addressed
 * and read only, so handing a copy out never puts the stored bytes at risk.
 */
final class OpenWith {

    private OpenWith() {}

    static String mimeOf(String name) {
        String lower = name == null ? "" : name.toLowerCase(Locale.ROOT);
        int dot = lower.lastIndexOf('.');
        String extension = dot >= 0 ? lower.substring(dot + 1) : "";
        switch (extension) {
            case "html": case "htm": return "text/html";
            case "pdf": return "application/pdf";
            case "txt": case "log": case "ini": case "conf": return "text/plain";
            case "md": case "markdown": return "text/markdown";
            case "csv": return "text/csv";
            case "json": return "application/json";
            case "xml": return "text/xml";
            case "svg": return "image/svg+xml";
            case "png": return "image/png";
            case "jpg": case "jpeg": return "image/jpeg";
            case "gif": return "image/gif";
            case "webp": return "image/webp";
            case "bmp": return "image/bmp";
            case "heic": case "heif": return "image/heic";
            case "mp4": case "m4v": return "video/mp4";
            case "mov": return "video/quicktime";
            case "webm": return "video/webm";
            case "mkv": return "video/x-matroska";
            case "3gp": return "video/3gpp";
            case "mp3": return "audio/mpeg";
            case "m4a": return "audio/mp4";
            case "wav": return "audio/wav";
            case "doc": return "application/msword";
            case "docx": return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
            case "xls": return "application/vnd.ms-excel";
            case "xlsx": return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
            case "ppt": return "application/vnd.ms-powerpoint";
            case "pptx": return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
            case "epub": return "application/epub+zip";
            case "zip": return "application/zip";
            default: return "application/octet-stream";
        }
    }

    static boolean isHtml(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        return lower.endsWith(".html") || lower.endsWith(".htm");
    }

    private static Intent viewIntent(Context context, Pack.Record record) {
        Uri uri = ShareProvider.uriFor(record);
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(uri, mimeOf(record.name));
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return intent;
    }

    /**
     * Browsers worth trying first. HTML in the system's minimal viewer loses all
     * styling, so we look for a real browser before falling back to a chooser.
     * Note that CATEGORY_BROWSABLE cannot be used here: it matches nothing for
     * content:// URIs, which would send every tap down the fallback path.
     */
    private static final String[] BROWSERS = {
            "com.android.chrome",
            "com.chrome.beta",
            "com.chrome.dev",
            "org.mozilla.firefox",
            "com.microsoft.emmx",
            "com.sec.android.app.sbrowser",
            "com.brave.browser",
            "com.opera.browser",
            "com.vivaldi.browser",
            "com.quark.browser",
            "com.UCMobile"
    };

    /** Returns false when nothing on the phone can show the file. */
    static boolean openInBrowser(Activity activity, Pack.Record record) {
        Uri uri = isHtml(record.name) ? fittedCopy(activity, record) : ShareProvider.uriFor(record);
        Intent base = new Intent(Intent.ACTION_VIEW);
        base.setDataAndType(uri, mimeOf(record.name));
        base.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);

        for (String pkg : BROWSERS) {
            Intent direct = new Intent(base).setPackage(pkg);
            if (activity.getPackageManager().resolveActivity(direct, 0) == null) continue;
            try {
                activity.startActivity(direct);
                return true;
            } catch (Exception ignored) {
                // Try the next browser rather than failing the whole gesture.
            }
        }
        if (activity.getPackageManager().resolveActivity(base, 0) == null) return false;
        try {
            activity.startActivity(Intent.createChooser(base, "打开方式"));
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * Hands the browser a phone-fitted copy rather than the original: the stored
     * object is immutable, and these pages are laid out for a 920 px desktop column.
     */
    private static Uri fittedCopy(Context context, Pack.Record record) {
        byte[] bytes = Html.read(Library.objectFile(context, record.sha), 8 * 1024 * 1024);
        if (bytes == null) return ShareProvider.uriFor(record);

        File target = ShareProvider.preparedFile(context, record.sha);
        File parent = target.getParentFile();
        if (parent != null && !parent.isDirectory() && !parent.mkdirs()) {
            return ShareProvider.uriFor(record);
        }
        try {
            OutputStream out = new java.io.FileOutputStream(target);
            try {
                out.write(Html.fit(Html.decode(bytes)).getBytes("UTF-8"));
            } finally {
                out.close();
            }
            return ShareProvider.preparedUriFor(record);
        } catch (Exception e) {
            return ShareProvider.uriFor(record);
        }
    }

    static boolean openWith(final Activity activity, final Pack.Record record) {
        Intent intent = viewIntent(activity, record);
        if (activity.getPackageManager().resolveActivity(intent, 0) == null) {
            noHandler(activity, record);
            return false;
        }
        try {
            activity.startActivity(Intent.createChooser(intent, "用其他应用打开"));
            return true;
        } catch (Exception e) {
            noHandler(activity, record);
            return false;
        }
    }

    private static String extensionOf(String name) {
        int dot = name.lastIndexOf('.');
        return dot >= 0 ? name.substring(dot).toLowerCase(Locale.ROOT) : "";
    }

    /**
     * "No apps can perform this action" is a dead end, and on a fresh phone it is
     * the normal case for a .docx. Offer the two answers that actually work: keep
     * a copy in 下载, or install something that can open it.
     */
    private static void noHandler(final Activity activity, final Pack.Record record) {
        String extension = extensionOf(record.name);
        new AlertDialog.Builder(activity)
                .setTitle("手机上还没有能打开 " + (extension.isEmpty() ? "这个格式" : extension) + " 的应用")
                .setMessage("文件本身是完整的。可以先保存到「下载」，装好 WPS 或 Office 之后再从那里打开；"
                        + "也可以现在去应用商店安装。")
                .setPositiveButton("保存到「下载」", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface dialog, int which) {
                        saveToDownloads(activity, record);
                    }
                })
                .setNeutralButton("去应用商店", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface dialog, int which) {
                        searchStore(activity, record);
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private static void searchStore(Activity activity, Pack.Record record) {
        String query = storeQuery(record.name);
        Intent market = new Intent(Intent.ACTION_VIEW,
                Uri.parse("market://search?q=" + Uri.encode(query)));
        try {
            activity.startActivity(market);
        } catch (Exception e) {
            Toast.makeText(activity, "请打开应用商店搜索「" + query + "」", Toast.LENGTH_LONG).show();
        }
    }

    private static String storeQuery(String name) {
        switch (extensionOf(name)) {
            case ".doc": case ".docx": case ".xls": case ".xlsx":
            case ".ppt": case ".pptx": case ".csv":
                return "WPS Office";
            case ".epub": return "EPUB 阅读器";
            case ".zip": case ".rar": case ".7z": return "解压缩";
            default: return "文件阅读器";
        }
    }

    static void show(final Activity activity, final Pack.Record record, boolean fromReader) {
        final boolean html = isHtml(record.name);
        String[] actions;
        if (fromReader) {
            actions = html
                    ? new String[]{"在浏览器中打开", "用其他应用打开…", "保存到「下载」"}
                    : new String[]{"用其他应用打开…", "保存到「下载」"};
        } else {
            actions = html
                    ? new String[]{"在浏览器中打开", "用其他应用打开…", "保存到「下载」", "在应用内阅读"}
                    : new String[]{"在应用内阅读", "用其他应用打开…", "保存到「下载」"};
        }
        new AlertDialog.Builder(activity)
                .setTitle(record.name)
                .setItems(actions, new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface dialog, int which) {
                        String action = actions[which];
                        if (action.equals("在浏览器中打开")) openInBrowser(activity, record);
                        else if (action.equals("用其他应用打开…")) openWith(activity, record);
                        else if (action.equals("保存到「下载」")) saveToDownloads(activity, record);
                        else if (action.equals("在应用内阅读")) ReaderActivity.open(activity, record.id);
                    }
                })
                .show();
    }

    /**
     * Writes a copy to the shared Downloads folder so the file survives the app and
     * can be opened by anything. On Android 10+ this needs no permission at all.
     */
    static void saveToDownloads(final Activity activity, final Pack.Record record) {
        final File source = Library.objectFile(activity, record.sha);
        if (!source.isFile()) {
            Toast.makeText(activity, "文件内容不在本机", Toast.LENGTH_SHORT).show();
            return;
        }
        new Thread(new Runnable() {
            public void run() {
                String message;
                try {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        ContentValues values = new ContentValues();
                        values.put(MediaStore.MediaColumns.DISPLAY_NAME, record.name);
                        values.put(MediaStore.MediaColumns.MIME_TYPE, mimeOf(record.name));
                        values.put(MediaStore.MediaColumns.RELATIVE_PATH,
                                Environment.DIRECTORY_DOWNLOADS + "/拾页");
                        values.put(MediaStore.MediaColumns.IS_PENDING, Integer.valueOf(1));
                        Uri target = activity.getContentResolver()
                                .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                        if (target == null) throw new IllegalStateException("无法在下载目录创建文件");
                        OutputStream out = activity.getContentResolver().openOutputStream(target);
                        if (out == null) throw new IllegalStateException("无法写入下载目录");
                        try {
                            copy(source, out);
                        } finally {
                            out.close();
                        }
                        values.clear();
                        values.put(MediaStore.MediaColumns.IS_PENDING, Integer.valueOf(0));
                        activity.getContentResolver().update(target, values, null, null);
                        message = "已保存到「下载 / 拾页」";
                    } else {
                        File folder = new File(activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "拾页");
                        if (!folder.isDirectory() && !folder.mkdirs()) throw new IllegalStateException("无法创建目录");
                        File target = new File(folder, record.name);
                        OutputStream out = new java.io.FileOutputStream(target);
                        try {
                            copy(source, out);
                        } finally {
                            out.close();
                        }
                        message = "已保存到 " + target.getAbsolutePath();
                    }
                } catch (Exception e) {
                    message = "保存失败：" + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage());
                }
                final String text = message;
                activity.runOnUiThread(new Runnable() {
                    public void run() {
                        Toast.makeText(activity, text, Toast.LENGTH_LONG).show();
                    }
                });
            }
        }).start();
    }

    private static void copy(File source, OutputStream out) throws Exception {
        InputStream in = new FileInputStream(source);
        try {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
        } finally {
            in.close();
        }
    }
}
