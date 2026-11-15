package local.shiyue.vault;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.ColorFilter;
import android.graphics.PixelFormat;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Small drawing helpers so the screens stay readable without a layout file. */
final class Ui {

    static final int PAPER = 0xFFF7F8FA, INK = 0xFF20332E, MUTED = 0xFF667B73;
    static final int TEAL = 0xFF26786A, SOFT = 0xFFE8F3EF, LINE = 0xFFE3E9E6;

    private Ui() {}

    static int dp(Context context, float value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }

    static GradientDrawable round(Context context, int fill, int radiusDp) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(fill);
        shape.setCornerRadius(dp(context, radiusDp));
        return shape;
    }

    static GradientDrawable round(Context context, int fill, int strokeColor, int radiusDp) {
        GradientDrawable shape = round(context, fill, radiusDp);
        shape.setStroke(dp(context, 1), strokeColor);
        return shape;
    }

    static TextView text(Context context, String value, float sizeSp, int color) {
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp);
        view.setTextColor(color);
        return view;
    }

    static TextView bold(TextView view) {
        view.setTypeface(Typeface.DEFAULT_BOLD);
        return view;
    }

    /** A filter chip: filled when selected, outlined when not. */
    static TextView chip(Context context, String label, boolean selected, int fill, int soft, int ink, int muted) {
        TextView view = text(context, label, 13f, selected ? 0xFFFFFFFF : ink);
        view.setGravity(Gravity.CENTER);
        view.setPadding(dp(context, 14), dp(context, 7), dp(context, 14), dp(context, 7));
        view.setBackground(selected ? round(context, fill, 18) : round(context, soft, 0xFFE3E9E6, 18));
        if (!selected) view.setTextColor(muted);
        view.setSingleLine(true);
        view.setMinHeight(dp(context, 44));
        view.setSelected(selected);
        view.setContentDescription(label + (selected ? "，已选中" : ""));
        return view;
    }

    static TextView iconButton(Context context, String name, String description) {
        TextView view = text(context, "", 14f, MUTED);
        view.setGravity(Gravity.CENTER);
        view.setMinWidth(dp(context, 44));
        view.setMinHeight(dp(context, 44));
        view.setBackground(new LineIcon(name, MUTED));
        view.setContentDescription(description);
        view.setFocusable(true);
        return view;
    }

    /** Uses the desktop's line weight and silhouettes without font glyph variants. */
    static final class LineIcon extends Drawable {
        private final String name;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        LineIcon(String name, int color) {
            this.name = name;
            paint.setColor(color); paint.setStrokeWidth(1.7f);
            paint.setStyle(Paint.Style.STROKE); paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
        }
        public void draw(Canvas c) {
            float side = Math.min(getBounds().width(), getBounds().height()) * .48f;
            c.save();
            c.translate(getBounds().centerX() - side / 2, getBounds().centerY() - side / 2);
            c.scale(side / 24, side / 24);
            Path p = new Path();
            if ("search".equals(name)) {
                c.drawCircle(10, 10, 7, paint); c.drawLine(15, 15, 21, 21, paint);
            } else if ("more".equals(name)) {
                c.drawPoint(5, 12, paint); c.drawPoint(12, 12, paint); c.drawPoint(19, 12, paint);
            } else if ("back".equals(name)) {
                p.moveTo(15, 5); p.lineTo(8, 12); p.lineTo(15, 19); c.drawPath(p, paint);
            } else if ("close".equals(name)) {
                c.drawLine(6, 6, 18, 18, paint); c.drawLine(18, 6, 6, 18, paint);
            } else if ("image".equals(name)) {
                c.drawRect(3, 4, 21, 20, paint); c.drawCircle(16, 8, 1, paint);
                p.moveTo(3, 16); p.lineTo(8, 11); p.lineTo(13, 16); p.lineTo(16, 13); p.lineTo(21, 18); c.drawPath(p, paint);
            } else if ("video".equals(name)) {
                c.drawRect(3, 6, 15, 18, paint);
                p.moveTo(15, 10); p.lineTo(21, 6); p.lineTo(21, 18); p.lineTo(15, 14); c.drawPath(p, paint);
            } else {
                p.moveTo(14, 3); p.lineTo(5, 3); p.lineTo(5, 21); p.lineTo(19, 21); p.lineTo(19, 8); p.close();
                p.moveTo(14, 3); p.lineTo(14, 8); p.lineTo(19, 8); c.drawPath(p, paint);
                c.drawLine(8, 12, 16, 12, paint); c.drawLine(8, 16, 14, 16, paint);
            }
            c.restore();
        }
        public void setAlpha(int value) { paint.setAlpha(value); }
        public void setColorFilter(ColorFilter filter) { paint.setColorFilter(filter); }
        public int getOpacity() { return PixelFormat.TRANSLUCENT; }
    }

    static LinearLayout.LayoutParams lp(int width, int height) {
        return new LinearLayout.LayoutParams(width, height);
    }

    static LinearLayout.LayoutParams lp(int width, int height, float weight) {
        return new LinearLayout.LayoutParams(width, height, weight);
    }

    static LinearLayout.LayoutParams margins(LinearLayout.LayoutParams params, Context context,
                                             float left, float top, float right, float bottom) {
        params.setMargins(dp(context, left), dp(context, top), dp(context, right), dp(context, bottom));
        return params;
    }
}
