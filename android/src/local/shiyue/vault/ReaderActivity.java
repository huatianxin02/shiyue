package local.shiyue.vault;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.pdf.PdfRenderer;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.MediaController;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.VideoView;

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
 * Reading view. Everything is rendered from the verified copy inside the app, so a
 * file that opened here is exactly the file that was packed on the Mac.
 */
public final class ReaderActivity extends Activity {

    private static final int MAX_TEXT = 2 * 1024 * 1024;

    private Pack.Record record;
    private File file;

    private FrameLayout stage;
    private LinearLayout pager;
    private TextView pageLabel;

    private PdfRenderer renderer;
    private ParcelFileDescriptor descriptor;
    private int page;
    private int pageCount;

    static void open(Activity activity, String id) {
        Intent intent = new Intent(activity, ReaderActivity.class);
        intent.putExtra("id", id);
        activity.startActivity(intent);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        String id = getIntent().getStringExtra("id");
        Library library = new Library(this);
        record = library.get(id);
        library.close();
        if (record == null) {
            finish();
            return;
        }
        file = Library.objectFile(this,record.sha);
        setContentView(buildUi());
        render();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        closePdf();
    }

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFFFFFFFF);
        root.setFitsSystemWindows(true);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setBackgroundColor(Ui.PAPER);
        header.setPadding(Ui.dp(this, 10), Ui.dp(this, 12), Ui.dp(this, 16), Ui.dp(this, 12));

        TextView back = Ui.iconButton(this, "back", "返回资料库");
        back.setGravity(Gravity.CENTER);
        back.setPadding(Ui.dp(this, 10), 0, Ui.dp(this, 10), Ui.dp(this, 4));
        back.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { finish(); }
        });
        header.addView(back);

        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        header.addView(titles, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView title = Ui.text(this, record.name, 15f, Ui.INK);
        title.setSingleLine(true);
        title.setEllipsize(TextUtils.TruncateAt.MIDDLE);
        titles.addView(title);

        String meta = FileAdapter.kindLabel(record.kind) + " · " + FileAdapter.readableSize(record.size)
                + (record.shelves.isEmpty() ? "" : " · " + join(record.shelves));
        TextView sub = Ui.text(this, meta, 11f, Ui.MUTED);
        sub.setSingleLine(true);
        sub.setEllipsize(TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams subParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        subParams.topMargin = Ui.dp(this, 2);
        titles.addView(sub, subParams);

        // Every format has at least one sensible "open elsewhere" answer, so this
        // stays visible rather than hiding inside a menu.
        TextView openWith = Ui.text(this, "打开方式", 12.5f, Ui.TEAL);
        openWith.setGravity(Gravity.CENTER);
        openWith.setPadding(Ui.dp(this, 12), Ui.dp(this, 7), Ui.dp(this, 12), Ui.dp(this, 7));
        openWith.setBackground(Ui.round(this, Ui.SOFT, 8));
        openWith.setMinHeight(Ui.dp(this, 44));
        openWith.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                OpenWith.show(ReaderActivity.this, record, true);
            }
        });
        header.addView(openWith);

        root.addView(header);

        stage = new FrameLayout(this);
        root.addView(stage, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        pager = new LinearLayout(this);
        pager.setOrientation(LinearLayout.HORIZONTAL);
        pager.setGravity(Gravity.CENTER_VERTICAL);
        pager.setBackgroundColor(Ui.PAPER);
        pager.setPadding(Ui.dp(this, 18), Ui.dp(this, 10), Ui.dp(this, 18), Ui.dp(this, 10));
        pager.setVisibility(View.GONE);

        TextView prev = Ui.text(this, "上一页", 13f, Ui.TEAL);
        prev.setMinHeight(Ui.dp(this, 44));
        prev.setGravity(Gravity.CENTER);
        prev.setPadding(Ui.dp(this, 12), Ui.dp(this, 6), Ui.dp(this, 12), Ui.dp(this, 6));
        prev.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { showPage(page - 1); }
        });
        pager.addView(prev);

        pageLabel = Ui.text(this, "", 12f, Ui.MUTED);
        pageLabel.setGravity(Gravity.CENTER);
        pager.addView(pageLabel, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView next = Ui.text(this, "下一页", 13f, Ui.TEAL);
        next.setMinHeight(Ui.dp(this, 44));
        next.setGravity(Gravity.CENTER);
        next.setPadding(Ui.dp(this, 12), Ui.dp(this, 6), Ui.dp(this, 12), Ui.dp(this, 6));
        next.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { showPage(page + 1); }
        });
        pager.addView(next);

        root.addView(pager);
        return root;
    }

    private static String join(java.util.List<String> values) {
        StringBuilder text = new StringBuilder();
        for (String value : values) {
            if (text.length() > 0) text.append(" / ");
            text.append(value);
        }
        return text.toString();
    }

    private void render() {
        if (!file.isFile()) {
            notice("文件内容不在本机，请重新导入资料包");
            return;
        }
        String name = record.name.toLowerCase(Locale.ROOT);
        if (name.endsWith(".png") || name.endsWith(".jpg") || name.endsWith(".jpeg")
                || name.endsWith(".gif") || name.endsWith(".webp") || name.endsWith(".bmp")) {
            showImage();
        } else if (name.endsWith(".mp4") || name.endsWith(".mov") || name.endsWith(".m4v")
                || name.endsWith(".webm") || name.endsWith(".3gp") || name.endsWith(".mkv")) {
            showVideo();
        } else if (name.endsWith(".pdf")) {
            showPdf();
        } else if (name.endsWith(".html") || name.endsWith(".htm")) {
            showHtml();
        } else if (Docx.isDocx(name)) {
            showDocx();
        } else if (isTextLike(name)) {
            showText();
        } else {
            unsupported();
        }
    }

    private static boolean isTextLike(String name) {
        String[] extensions = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yml",
                ".yaml", ".log", ".ini", ".conf", ".css", ".js", ".ts", ".py", ".java", ".kt",
                ".sh", ".sql", ".srt", ".vtt", ".svg"};
        for (String extension : extensions) if (name.endsWith(extension)) return true;
        return false;
    }

    // --------------------------------------------------------------- images

    private void showImage() {
        ImageView view = new ImageView(this);
        view.setScaleType(ImageView.ScaleType.FIT_CENTER);
        view.setBackgroundColor(0xFFF2F4F3);
        Bitmap bitmap = decode(file);
        if (bitmap == null) {
            notice("这张图片无法解码");
            return;
        }
        view.setImageBitmap(bitmap);
        stage.addView(view, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private Bitmap decode(File source) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(source.getAbsolutePath(), bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null;

        int screen = getResources().getDisplayMetrics().widthPixels;
        int sample = 1;
        while (bounds.outWidth / (sample * 2) >= screen) sample *= 2;

        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        options.inPreferredConfig = Bitmap.Config.ARGB_8888;
        try {
            return Thumbs.orient(source,BitmapFactory.decodeFile(source.getAbsolutePath(), options));
        } catch (OutOfMemoryError e) {
            options.inSampleSize = sample * 2;
            try{return Thumbs.orient(source,BitmapFactory.decodeFile(source.getAbsolutePath(), options));}catch(OutOfMemoryError stillTooLarge){return null;}
        }
    }

    // ---------------------------------------------------------------- video

    private void showVideo() {
        final VideoView view = new VideoView(this);
        view.setBackgroundColor(0xFF101615);
        MediaController controller = new MediaController(this);
        controller.setAnchorView(view);
        view.setMediaController(controller);
        view.setVideoPath(file.getAbsolutePath());
        view.setOnPreparedListener(new android.media.MediaPlayer.OnPreparedListener() {
            public void onPrepared(android.media.MediaPlayer player) {
                // Show the first frame instead of a black rectangle.
                view.seekTo(1);
            }
        });
        view.setOnErrorListener(new android.media.MediaPlayer.OnErrorListener() {
            public boolean onError(android.media.MediaPlayer player, int what, int extra) {
                notice("这段视频的编码手机无法播放\n\n文件本身是完整的，可以在电脑上打开。");
                return true;
            }
        });
        stage.addView(view, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    // ------------------------------------------------------------------ pdf

    private void showPdf() {
        try {
            descriptor = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
            renderer = new PdfRenderer(descriptor);
            pageCount = renderer.getPageCount();
            if (pageCount <= 0) {
                notice("这个 PDF 没有页面");
                return;
            }
            pager.setVisibility(View.VISIBLE);
            showPage(getSharedPreferences("reading",MODE_PRIVATE).getInt(record.sha,0));
        } catch (Exception e) {
            notice("无法打开这个 PDF：" + e.getMessage());
        }
    }

    private void showPage(int index) {
        if (renderer == null) return;
        page = Math.max(0, Math.min(index, pageCount - 1));
        getSharedPreferences("reading",MODE_PRIVATE).edit().putInt(record.sha,page).apply();
        try {
            PdfRenderer.Page current = renderer.openPage(page);
            int width = Math.max(getResources().getDisplayMetrics().widthPixels - Ui.dp(this, 24), 320);
            float scale = width / (float) current.getWidth();
            int height = Math.max((int) (current.getHeight() * scale), 1);
            Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
            bitmap.eraseColor(Color.WHITE);
            current.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY);
            current.close();

            stage.removeAllViews();
            ImageView view = new ImageView(this);
            view.setImageBitmap(bitmap);
            view.setScaleType(ImageView.ScaleType.FIT_CENTER);
            view.setBackgroundColor(0xFFF2F4F3);
            stage.addView(view, new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            pageLabel.setText((page + 1) + " / " + pageCount);
        } catch (Exception e) {
            notice("这一页无法显示：" + e.getMessage());
        }
    }

    private void closePdf() {
        if (renderer != null) {
            renderer.close();
            renderer = null;
        }
        if (descriptor != null) {
            try {
                descriptor.close();
            } catch (Exception ignored) {
            }
            descriptor = null;
        }
    }

    // ----------------------------------------------------------------- text

    private void showText() {
        String text = readText();
        if (text == null) {
            notice("这个文件看起来不是文本，或体积过大");
            return;
        }
        showPlain(text, true, null);
    }

    /**
     * A .docx has no renderer in the framework, but its words do not need one:
     * extracting word/document.xml keeps the document readable offline, and the
     * layout stays the office app's job.
     */
    private void showDocx() {
        String text = Docx.text(file, MAX_TEXT);
        if (text == null) {
            unsupported();
            return;
        }
        showPlain(text, false, "Word 文档的纯文字预览 · 图片与排版不在这里显示");
    }

    private void showPlain(String text, boolean monospace, String banner) {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);

        if (banner != null) {
            TextView note = Ui.text(this, banner, 11.5f, 0xFF6C817B);
            note.setBackgroundColor(0xFFF1F5F4);
            note.setPadding(Ui.dp(this, 18), Ui.dp(this, 8), Ui.dp(this, 18), Ui.dp(this, 8));
            column.addView(note);
        }

        ScrollView scroll = new ScrollView(this);
        scroll.setPadding(Ui.dp(this, 18), Ui.dp(this, 16), Ui.dp(this, 18), Ui.dp(this, 28));
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, monospace ? 14f : 15f);
        view.setTextColor(0xFF22302C);
        if (monospace) view.setTypeface(android.graphics.Typeface.MONOSPACE);
        view.setLineSpacing(Ui.dp(this, monospace ? 5 : 7), 1f);
        view.setTextIsSelectable(true);
        scroll.addView(view);
        column.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        stage.addView(column, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    private void showHtml() {
        String html = readText();
        if (html == null) {
            notice("这个网页文件无法读取");
            return;
        }
        WebView view = new WebView(this);
        WebSettings settings = view.getSettings();
        // A packed page is untrusted input: no scripts, no network, no file access.
        settings.setJavaScriptEnabled(false);
        settings.setBlockNetworkLoads(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setLoadsImagesAutomatically(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setDefaultTextEncodingName("UTF-8");
        view.setBackgroundColor(0xFFFFFFFF);
        view.loadDataWithBaseURL(null, Html.fit(html), "text/html", "UTF-8", null);
        stage.addView(view, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    /** Nothing in-app renders this format, so offer the two useful exits instead. */
    private void unsupported() {
        stage.removeAllViews();
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(Ui.dp(this, 34), 0, Ui.dp(this, 34), 0);

        TextView mark = Ui.text(this, "⇱", 30f, 0xFFB9C6C1);
        mark.setGravity(Gravity.CENTER);
        box.addView(mark);

        String extension = record.name.lastIndexOf('.') >= 0
                ? record.name.substring(record.name.lastIndexOf('.')).toLowerCase(Locale.ROOT) : "";
        TextView message = Ui.text(this,
                "应用内没有 " + extension + " 的阅读器\n\n"
                        + "文件是完整的。可以交给手机上已安装的应用打开，"
                        + "或保存到「下载」后再用电脑处理。",
                13.5f, Ui.MUTED);
        message.setGravity(Gravity.CENTER);
        message.setLineSpacing(Ui.dp(this, 5), 1f);
        LinearLayout.LayoutParams messageParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        messageParams.topMargin = Ui.dp(this, 12);
        box.addView(message, messageParams);

        TextView open = Ui.text(this, "用其他应用打开", 13.5f, 0xFFFFFFFF);
        open.setGravity(Gravity.CENTER);
        open.setPadding(Ui.dp(this, 22), Ui.dp(this, 11), Ui.dp(this, 22), Ui.dp(this, 11));
        open.setBackground(Ui.round(this, Ui.TEAL, 20));
        open.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { OpenWith.openWith(ReaderActivity.this, record); }
        });
        LinearLayout.LayoutParams openParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        openParams.topMargin = Ui.dp(this, 22);
        box.addView(open, openParams);

        TextView save = Ui.text(this, "保存到「下载」", 13.5f, Ui.TEAL);
        save.setGravity(Gravity.CENTER);
        save.setPadding(Ui.dp(this, 22), Ui.dp(this, 10), Ui.dp(this, 22), Ui.dp(this, 10));
        save.setBackground(Ui.round(this, Ui.SOFT, 20));
        save.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { OpenWith.saveToDownloads(ReaderActivity.this, record); }
        });
        LinearLayout.LayoutParams saveParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        saveParams.topMargin = Ui.dp(this, 10);
        box.addView(save, saveParams);

        stage.addView(box, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    /** Reads the file as text, or null when it is binary or oversized. */
    private String readText() {
        byte[] bytes = Html.read(file, MAX_TEXT);
        return bytes == null ? null : Html.decode(bytes);
    }

    // --------------------------------------------------------------- notice

    private void notice(String message) {
        stage.removeAllViews();
        TextView view = Ui.text(this, message, 14f, Ui.MUTED);
        view.setGravity(Gravity.CENTER);
        view.setLineSpacing(Ui.dp(this, 5), 1f);
        view.setPadding(Ui.dp(this, 36), 0, Ui.dp(this, 36), 0);
        stage.addView(view, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }
}
