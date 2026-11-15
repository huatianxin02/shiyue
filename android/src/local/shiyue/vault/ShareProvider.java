package local.shiyue.vault;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.List;

/**
 * Hands a single verified object to another app.
 *
 * The library lives in app-private storage, so nothing outside can read it. This
 * provider exposes one file at a time, only to the app the user picked, and only
 * for reading: the grant is attached to the intent, never to the provider itself
 * (`exported="false"`).
 */
public final class ShareProvider extends ContentProvider {

    static final String AUTHORITY = "local.shiyue.vault.share";

    static Uri uriFor(Pack.Record record) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(record.sha)
                .appendPath(record.name)
                .build();
    }

    /** A phone-fitted copy of an HTML page, written to the cache before sharing. */
    static Uri preparedUriFor(Pack.Record record) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath("prepared")
                .appendPath(record.sha)
                .appendPath(record.name)
                .build();
    }

    static java.io.File preparedFile(Context context, String sha) {
        return new java.io.File(new java.io.File(context.getCacheDir(), "prepared"), sha + ".html");
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        List<String> segments = uri.getPathSegments();
        String name = segments.isEmpty() ? "" : segments.get(segments.size() - 1);
        return OpenWith.mimeOf(name);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String order) {
        List<String> segments = uri.getPathSegments();
        if (segments.isEmpty()) return null;
        File file = resolve(segments);
        if (file == null || !file.isFile()) return null;

        String[] columns = projection != null ? projection
                : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor cursor = new MatrixCursor(columns, 1);
        Object[] row = new Object[columns.length];
        String display = segments.get(segments.size() - 1);
        for (int i = 0; i < columns.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(columns[i])) {
                row[i] = display;
            } else if (OpenableColumns.SIZE.equals(columns[i])) {
                row[i] = Long.valueOf(file.length());
            } else {
                row[i] = null;
            }
        }
        cursor.addRow(row);
        return cursor;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File file = resolve(uri.getPathSegments());
        if (file == null || !file.isFile()) throw new FileNotFoundException("文件不存在");
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    /**
     * Only `objects/<64 hex>` and the cache's `prepared/<64 hex>` are reachable, so a
     * crafted URI cannot walk out of either directory.
     */
    private File resolve(List<String> segments) {
        if (segments.isEmpty()) return null;
        if (segments.size() >= 2 && "prepared".equals(segments.get(0))) {
            String sha = segments.get(1);
            if (!sha.matches("[0-9a-f]{64}")) return null;
            return preparedFile(getContext(), sha);
        }
        String sha = segments.get(0);
        if (!sha.matches("[0-9a-f]{64}")) return null;
        return Library.objectFile(getContext(), sha);
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("只读");
    }

    @Override
    public int delete(Uri uri, String selection, String[] args) {
        throw new UnsupportedOperationException("只读");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] args) {
        throw new UnsupportedOperationException("只读");
    }
}
