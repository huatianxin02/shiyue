package local.shiyue.vault;

import android.content.Context;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.ImageView;
import java.util.Set;
import java.util.HashSet;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/** One card per file: what it is, how big, when it arrived, where it is filed. */
final class FileAdapter extends BaseAdapter {

    interface Listener {
        void onOpen(Pack.Record record);
        void onActions(Pack.Record record);
    }

    private final Context context;
    private final Listener listener;
    private final Set<String> selected=new HashSet<String>();
    void setSelection(Set<String> ids){selected.clear();selected.addAll(ids);notifyDataSetChanged();}
    private final List<Pack.Record> rows = new ArrayList<Pack.Record>();
    private final SimpleDateFormat parser = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US);
    private final SimpleDateFormat printer = new SimpleDateFormat("MM-dd", Locale.CHINA);

    FileAdapter(Context context, Listener listener) {
        this.context = context;
        this.listener = listener;
        parser.setTimeZone(TimeZone.getTimeZone("UTC"));
    }

    void setRows(List<Pack.Record> records) {
        rows.clear();
        rows.addAll(records);
        notifyDataSetChanged();
    }

    public int getCount() { return rows.size(); }

    public Object getItem(int position) { return rows.get(position); }

    public long getItemId(int position) { return position; }

    public View getView(int position, View convert, ViewGroup parent) {
        final Pack.Record record = rows.get(position);
        Holder holder;
        if (convert == null) {
            FrameLayout wrap = new FrameLayout(context);
            wrap.setPadding(0, 0, 0, Ui.dp(context, 8));

            LinearLayout card = new LinearLayout(context);
            card.setOrientation(LinearLayout.HORIZONTAL);
            card.setGravity(Gravity.CENTER_VERTICAL);
            card.setPadding(Ui.dp(context, 14), Ui.dp(context, 12), Ui.dp(context, 14), Ui.dp(context, 12));
            card.setBackground(Ui.round(context, 0xFFFFFFFF, Ui.LINE, 12));

            ImageView badge = new ImageView(context);
            badge.setScaleType(ImageView.ScaleType.CENTER_CROP);
            badge.setClipToOutline(true);
            LinearLayout.LayoutParams badgeParams = new LinearLayout.LayoutParams(Ui.dp(context, 40), Ui.dp(context, 40));
            card.addView(badge, badgeParams);

            LinearLayout middle = new LinearLayout(context);
            middle.setOrientation(LinearLayout.VERTICAL);
            LinearLayout.LayoutParams middleParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            middleParams.setMargins(Ui.dp(context, 13), 0, Ui.dp(context, 8), 0);
            card.addView(middle, middleParams);

            TextView name = Ui.text(context, "", 15f, Ui.INK);
            name.setSingleLine(true);
            name.setEllipsize(android.text.TextUtils.TruncateAt.END);
            middle.addView(name);

            TextView meta = Ui.text(context, "", 11.5f, Ui.MUTED);
            meta.setSingleLine(true);
            LinearLayout.LayoutParams metaParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            metaParams.topMargin = Ui.dp(context, 3);
            middle.addView(meta, metaParams);

            TextView shelf = Ui.text(context, "", 11f, Ui.TEAL);
            shelf.setSingleLine(true);
            shelf.setMaxWidth(Ui.dp(context, 72));
            shelf.setEllipsize(android.text.TextUtils.TruncateAt.END);
            card.addView(shelf);

            wrap.addView(card, new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

            holder = new Holder(badge, name, meta, shelf);
            wrap.setTag(holder);
            wrap.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) {
                    Object tag = v.getTag();
                    if (tag instanceof Holder && ((Holder) tag).record != null) listener.onOpen(((Holder) tag).record);
                }
            });
            wrap.setOnLongClickListener(new View.OnLongClickListener() {
                public boolean onLongClick(View v) {
                    Object tag = v.getTag();
                    if (tag instanceof Holder && ((Holder) tag).record != null) {
                        listener.onActions(((Holder) tag).record);
                        return true;
                    }
                    return false;
                }
            });
            convert = wrap;
            holder = (Holder) wrap.getTag();
        } else {
            holder = (Holder) convert.getTag();
        }

        holder.record = record;
        holder.name.setText((selected.contains(record.id)?"✓ ":record.starred!=0?"★ ":"")+record.name);
        convert.setBackgroundColor(selected.contains(record.id)?Ui.SOFT:android.graphics.Color.TRANSPARENT);
        holder.badge.setBackground(new android.graphics.drawable.LayerDrawable(new android.graphics.drawable.Drawable[] {Ui.round(context, kindSoft(record.kind), 8), new Ui.LineIcon(record.kind, kindInk(record.kind))}));
        holder.badge.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        Thumbs.load(context,record,holder.badge);
        holder.meta.setText(metaLine(record));
        if (record.shelves.isEmpty()) {
            holder.shelf.setVisibility(View.GONE);
        } else {
            holder.shelf.setVisibility(View.VISIBLE);
            holder.shelf.setText(record.shelves.get(0));
        }
        return convert;
    }

    private String metaLine(Pack.Record record) {
        StringBuilder text = new StringBuilder(kindLabel(record.kind));
        text.append(" · ").append(readableSize(record.size));
        String when = shortDate(record.addedAt);
        if (when.length() > 0) text.append(" · ").append(when);
        if (!record.tags.isEmpty()) {
            String[] tags = record.tags.split("\u0001");
            if (tags.length > 0 && tags[0].length() > 0) text.append(" · #").append(tags[0]);
        }
        return text.toString();
    }

    private String shortDate(String addedAt) {
        if (addedAt == null || addedAt.length() < 19) return "";
        try {
            Date date = parser.parse(addedAt.substring(0, 19));
            return date == null ? "" : printer.format(date);
        } catch (Exception e) {
            return "";
        }
    }

    static String readableSize(long bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024L * 1024) return String.format(Locale.CHINA, "%.0f KB", bytes / 1024.0);
        if (bytes < 1024L * 1024 * 1024) return String.format(Locale.CHINA, "%.1f MB", bytes / (1024.0 * 1024));
        return String.format(Locale.CHINA, "%.2f GB", bytes / (1024.0 * 1024 * 1024));
    }

    static String kindLabel(String kind) {
        if ("image".equals(kind)) return "图片";
        if ("video".equals(kind)) return "视频";
        return "文档";
    }

    static String badgeLetter(String kind) {
        if ("image".equals(kind)) return "图";
        if ("video".equals(kind)) return "影";
        return "文";
    }

    static int kindInk(String kind) {
        if ("image".equals(kind)) return 0xFFB26A3C;
        if ("video".equals(kind)) return 0xFF4A5C8A;
        return Ui.TEAL;
    }

    static int kindSoft(String kind) {
        if ("image".equals(kind)) return 0xFFFBEDE3;
        if ("video".equals(kind)) return 0xFFE9EDF7;
        return Ui.SOFT;
    }

    private static final class Holder {
        final ImageView badge;
        final TextView name;
        final TextView meta;
        final TextView shelf;
        Pack.Record record;

        Holder(ImageView badge, TextView name, TextView meta, TextView shelf) {
            this.badge = badge;
            this.name = name;
            this.meta = meta;
            this.shelf = shelf;
        }
    }
}
