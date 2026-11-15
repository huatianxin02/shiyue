package local.shiyue.vault;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import java.io.File;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The phone's copy of the library: an index plus the verified bytes.
 *
 * The schema is deliberately close to the Mac app's, but search here is a plain
 * substring match rather than the desktop's token index. On a phone the library is
 * whatever fits in a pack, so scanning names and tags is fast enough and needs no
 * FTS table that Android may not have compiled in.
 */
final class Library implements Pack.Store {

    private final File root;
    private final File objects;
    private final SQLiteDatabase db;

    Library(Context context) {
        root = new File(context.getFilesDir(), "library");
        objects = new File(root, "objects");
        if (!objects.isDirectory() && !objects.mkdirs()) {
            throw new IllegalStateException("无法创建存储目录");
        }
        db = SQLiteDatabase.openOrCreateDatabase(new File(root, "index.db"), null);
        // PRAGMA returns a row, so it goes through rawQuery rather than execSQL.
        Cursor wal = db.rawQuery("PRAGMA journal_mode=WAL", null);
        wal.moveToFirst();
        wal.close();
        db.execSQL("CREATE TABLE IF NOT EXISTS files("
                + "id TEXT PRIMARY KEY,"
                + "sha TEXT NOT NULL,"
                + "name TEXT NOT NULL,"
                + "kind TEXT NOT NULL DEFAULT 'document',"
                + "size INTEGER NOT NULL DEFAULT 0,"
                + "tags TEXT NOT NULL DEFAULT '',"
                + "starred INTEGER NOT NULL DEFAULT 0,"
                + "added_at TEXT NOT NULL DEFAULT '',"
                + "source TEXT NOT NULL DEFAULT '')");
        db.execSQL("CREATE TABLE IF NOT EXISTS shelves("
                + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                + "name TEXT NOT NULL COLLATE NOCASE UNIQUE)");
        db.execSQL("CREATE TABLE IF NOT EXISTS membership("
                + "file_id TEXT NOT NULL,"
                + "shelf_id INTEGER NOT NULL,"
                + "PRIMARY KEY(file_id, shelf_id))");
        db.execSQL("CREATE INDEX IF NOT EXISTS files_kind ON files(kind)");
        db.execSQL("CREATE INDEX IF NOT EXISTS files_added ON files(added_at)");
        db.execSQL("CREATE INDEX IF NOT EXISTS membership_shelf ON membership(shelf_id)");
        db.execSQL("CREATE TABLE IF NOT EXISTS trash(id TEXT PRIMARY KEY,sha TEXT NOT NULL,payload TEXT NOT NULL,size INTEGER NOT NULL)");
        // Self-healing: an earlier build keyed rows by pack id, so importing two packs
        // that carried the same file left two rows for one blob. Keep the oldest.
        db.execSQL("INSERT OR IGNORE INTO membership SELECT keep.id,m.shelf_id FROM files f JOIN membership m ON m.file_id=f.id JOIN files keep ON keep.rowid=(SELECT MIN(x.rowid) FROM files x WHERE x.sha=f.sha)");
        db.execSQL("DELETE FROM files WHERE rowid NOT IN (SELECT MIN(rowid) FROM files GROUP BY sha)");
        db.execSQL("DELETE FROM membership WHERE file_id NOT IN (SELECT id FROM files)");
    }

    // ------------------------------------------------------------ Pack.Store

    public File objectPath(String sha) {
        return new File(new File(objects, sha.substring(0, 2)), sha);
    }

    /** Where a stored object lives, for callers that only have a Context. */
    static File objectFile(Context context, String sha) {
        File objects = new File(new File(context.getFilesDir(), "library"), "objects");
        return new File(new File(objects, sha.substring(0, 2)), sha);
    }

    public boolean hasObject(String sha) {
        return objectPath(sha).isFile();
    }

    public List<Pack.Record> all() {
        return query("", 0, "");
    }

    public void put(Pack.Record record) {
        // Two packs can carry the same content under different record ids, because
        // each pack is written by whichever app held the file first. The library is
        // content addressed, so identical bytes are one entry: fold the incoming
        // record into the row that already holds that SHA.
        String id = record.id;
        Pack.Record local = null;
        Cursor same = db.rawQuery("SELECT id FROM files WHERE sha=? LIMIT 1", new String[]{record.sha});
        try { if (same.moveToFirst()) local = get(same.getString(0)); }
        finally { same.close(); }
        boolean merged = local != null;
        if (merged) id = local.id;
        else {
            Pack.Record collision = get(id);
            if (collision != null && !collision.sha.equals(record.sha))
                id = java.util.UUID.nameUUIDFromBytes((id + record.sha).getBytes(java.nio.charset.StandardCharsets.UTF_8)).toString().replace("-", "");
        }
        Pack.Record metadata = merged ? local : record;
        ContentValues values = new ContentValues();
        values.put("id", id);
        values.put("sha", record.sha);
        values.put("name", metadata.name);
        values.put("kind", normalizeKind(metadata.kind));
        values.put("size", metadata.size);
        values.put("tags", metadata.tags == null ? "" : metadata.tags);
        values.put("starred", metadata.starred);
        values.put("added_at", metadata.addedAt == null ? "" : metadata.addedAt);
        values.put("source", metadata.source == null ? "" : metadata.source);
        db.insertWithOnConflict("files", null, values, SQLiteDatabase.CONFLICT_REPLACE);
        // A fresh record owns its shelf list; a merged one only contributes shelves.
        if (!merged) db.delete("membership", "file_id=?", new String[]{id});
        for (String name : record.shelves) {
            long shelf = ensureShelf(name);
            if (shelf <= 0) continue;
            ContentValues link = new ContentValues();
            link.put("file_id", id);
            link.put("shelf_id", shelf);
            db.insertWithOnConflict("membership", null, link, SQLiteDatabase.CONFLICT_IGNORE);
        }
    }

    public void beginImport() { db.beginTransaction(); }
    public void commitImport() { db.setTransactionSuccessful(); db.endTransaction(); }
    public void rollbackImport() { if (db.inTransaction()) db.endTransaction(); }
    void close() { db.close(); }
    void edit(String id, String tags, Boolean starred) {
        ContentValues values = new ContentValues();
        if (tags != null) values.put("tags", tags);
        if (starred != null) values.put("starred", starred ? 1 : 0);
        if (values.size() > 0) db.update("files", values, "id=?", new String[]{id});
    }

    Pack.Store subset(final List<String> ids) {
        final Library self=this;
        return new Pack.Store(){
            public File objectPath(String sha){return self.objectPath(sha);}
            public boolean hasObject(String sha){return self.hasObject(sha);}
            public void put(Pack.Record r){throw new UnsupportedOperationException();}
            public List<Pack.Record> all(){List<Pack.Record> out=new ArrayList<Pack.Record>();for(String id:ids){Pack.Record r=get(id);if(r!=null)out.add(r);}return out;}
        };
    }

    static String normalizeKind(String kind) {
        if ("image".equals(kind) || "video".equals(kind)) return kind;
        return "document";
    }

    // -------------------------------------------------------------- reading

    List<Pack.Record> query(String kind, long shelf, String text) {
        StringBuilder sql = new StringBuilder("SELECT f.id,f.sha,f.name,f.kind,f.size,f.tags,f.starred,f.added_at,f.source FROM files f");
        List<String> args = new ArrayList<String>();
        if (shelf > 0) {
            sql.append(" JOIN membership m ON m.file_id=f.id AND m.shelf_id=?");
            args.add(String.valueOf(shelf));
        }
        sql.append(" WHERE 1=1");
        if (kind != null && kind.length() > 0) {
            sql.append(" AND f.kind=?");
            args.add(kind);
        }
        String needle = text == null ? "" : text.trim();
        if (needle.length() > 0) {
            sql.append(" AND (f.name LIKE ? ESCAPE '\\' OR f.tags LIKE ? ESCAPE '\\')");
            String pattern = "%" + escapeLike(needle) + "%";
            args.add(pattern);
            args.add(pattern);
        }
        sql.append(" ORDER BY f.added_at DESC, f.name ASC");

        Map<String, Pack.Record> found = new LinkedHashMap<String, Pack.Record>();
        Cursor cursor = db.rawQuery(sql.toString(), args.toArray(new String[0]));
        try {
            while (cursor.moveToNext()) {
                Pack.Record record = read(cursor);
                found.put(record.id, record);
            }
        } finally {
            cursor.close();
        }
        List<Pack.Record> records = new ArrayList<Pack.Record>(found.values());
        Map<String, List<String>> shelves = shelvesByFile();
        for (Pack.Record record : records) {
            List<String> names = shelves.get(record.id);
            if (names != null) record.shelves.addAll(names);
        }
        return records;
    }

    Pack.Record get(String id) {
        Cursor cursor = db.rawQuery("SELECT id,sha,name,kind,size,tags,starred,added_at,source FROM files WHERE id=?",
                new String[]{id});
        try {
            if (!cursor.moveToFirst()) return null;
            Pack.Record record = read(cursor);
            record.shelves.addAll(shelfNames(id));
            return record;
        } finally {
            cursor.close();
        }
    }

    private static Pack.Record read(Cursor cursor) {
        Pack.Record record = new Pack.Record();
        record.id = cursor.getString(0);
        record.sha = cursor.getString(1);
        record.name = cursor.getString(2);
        record.kind = cursor.getString(3);
        record.size = cursor.getLong(4);
        record.tags = cursor.getString(5);
        record.starred = cursor.getInt(6);
        record.addedAt = cursor.getString(7);
        record.source = cursor.getString(8);
        return record;
    }

    private Map<String, List<String>> shelvesByFile() {
        Map<String, List<String>> map = new LinkedHashMap<String, List<String>>();
        Cursor cursor = db.rawQuery("SELECT m.file_id,s.name FROM membership m JOIN shelves s ON s.id=m.shelf_id ORDER BY s.name", null);
        try {
            while (cursor.moveToNext()) {
                String fileId = cursor.getString(0);
                List<String> names = map.get(fileId);
                if (names == null) {
                    names = new ArrayList<String>();
                    map.put(fileId, names);
                }
                names.add(cursor.getString(1));
            }
        } finally {
            cursor.close();
        }
        return map;
    }

    List<String> shelfNames(String fileId) {
        List<String> names = new ArrayList<String>();
        Cursor cursor = db.rawQuery("SELECT s.name FROM membership m JOIN shelves s ON s.id=m.shelf_id WHERE m.file_id=? ORDER BY s.name",
                new String[]{fileId});
        try {
            while (cursor.moveToNext()) names.add(cursor.getString(0));
        } finally {
            cursor.close();
        }
        return names;
    }

    static final class Shelf {
        long id;
        String name;
        int count;
    }

    List<Shelf> shelves() {
        List<Shelf> shelves = new ArrayList<Shelf>();
        Cursor cursor = db.rawQuery("SELECT s.id,s.name,"
                + "(SELECT COUNT(*) FROM membership m WHERE m.shelf_id=s.id) "
                + "FROM shelves s ORDER BY s.name", null);
        try {
            while (cursor.moveToNext()) {
                Shelf shelf = new Shelf();
                shelf.id = cursor.getLong(0);
                shelf.name = cursor.getString(1);
                shelf.count = cursor.getInt(2);
                shelves.add(shelf);
            }
        } finally {
            cursor.close();
        }
        return shelves;
    }

    /** total, document, image, video */
    int[] counts() {
        int[] counts = new int[4];
        Cursor cursor = db.rawQuery("SELECT kind,COUNT(*) FROM files GROUP BY kind", null);
        try {
            while (cursor.moveToNext()) {
                String kind = cursor.getString(0);
                int value = cursor.getInt(1);
                counts[0] += value;
                if ("document".equals(kind)) counts[1] = value;
                else if ("image".equals(kind)) counts[2] = value;
                else if ("video".equals(kind)) counts[3] = value;
            }
        } finally {
            cursor.close();
        }
        return counts;
    }

    // -------------------------------------------------------------- editing

    long ensureShelf(String name) {
        String clean = cleanShelf(name);
        if (clean.length() == 0) return -1;
        Cursor cursor = db.rawQuery("SELECT id FROM shelves WHERE name=?", new String[]{clean});
        try {
            if (cursor.moveToFirst()) return cursor.getLong(0);
        } finally {
            cursor.close();
        }
        ContentValues values = new ContentValues();
        values.put("name", clean);
        return db.insert("shelves", null, values);
    }

    /** Returns the new id, or -1 when the name is empty or already used. */
    long createShelf(String name) {
        String clean = cleanShelf(name);
        if (clean.length() == 0) return -1;
        Cursor cursor = db.rawQuery("SELECT id FROM shelves WHERE name=?", new String[]{clean});
        try {
            if (cursor.moveToFirst()) return -1;
        } finally {
            cursor.close();
        }
        ContentValues values = new ContentValues();
        values.put("name", clean);
        return db.insert("shelves", null, values);
    }

    boolean renameShelf(long id, String name) {
        String clean = cleanShelf(name);
        if (clean.length() == 0) return false;
        Cursor cursor = db.rawQuery("SELECT id FROM shelves WHERE name=? AND id<>?", new String[]{clean, String.valueOf(id)});
        try {
            if (cursor.moveToFirst()) return false;
        } finally {
            cursor.close();
        }
        ContentValues values = new ContentValues();
        values.put("name", clean);
        db.update("shelves", values, "id=?", new String[]{String.valueOf(id)});
        return true;
    }

    void deleteShelf(long id) {
        db.delete("membership", "shelf_id=?", new String[]{String.valueOf(id)});
        db.delete("shelves", "id=?", new String[]{String.valueOf(id)});
    }

    void assign(long shelf, List<String> fileIds) {
        for (String fileId : fileIds) {
            ContentValues values = new ContentValues();
            values.put("file_id", fileId);
            values.put("shelf_id", shelf);
            db.insertWithOnConflict("membership", null, values, SQLiteDatabase.CONFLICT_IGNORE);
        }
    }

    void unassign(long shelf, List<String> fileIds) {
        for (String fileId : fileIds) {
            db.delete("membership", "file_id=? AND shelf_id=?", new String[]{fileId, String.valueOf(shelf)});
        }
    }

    void remove(List<String> fileIds) {
        db.beginTransaction();
        try {
            for(String id:fileIds){
                Pack.Record r=get(id);if(r==null)continue;
                ContentValues values=new ContentValues();values.put("id",java.util.UUID.randomUUID().toString());values.put("sha",r.sha);values.put("payload",Json.write(r.toJson()));values.put("size",r.size);
                db.insertOrThrow("trash",null,values);
                db.delete("membership","file_id=?",new String[]{id});db.delete("files","id=?",new String[]{id});
            }
            db.setTransactionSuccessful();
        } finally {db.endTransaction();}
    }
    List<Map<String,Object>> trashRows(){
        List<Map<String,Object>> rows=new ArrayList<Map<String,Object>>();Cursor c=db.rawQuery("SELECT id,payload FROM trash ORDER BY rowid DESC",null);
        try{while(c.moveToNext()){Map<String,Object> row=new LinkedHashMap<String,Object>();row.put("id",c.getString(0));row.put("record",Json.asMap(Json.parse(c.getString(1))));rows.add(row);}}
        finally{c.close();}return rows;
    }
    void restoreTrash(String id){
        db.beginTransaction();
        try{
            Cursor c=db.rawQuery("SELECT payload FROM trash WHERE id=?",new String[]{id});Map<String,Object> m;
            try{if(!c.moveToFirst())return;m=Json.asMap(Json.parse(c.getString(0)));}finally{c.close();}
            Pack.Record r=new Pack.Record();r.id=Json.str(m,"id","");r.sha=Json.str(m,"sha","");r.name=Json.str(m,"name","");r.kind=Json.str(m,"kind","document");r.addedAt=Json.str(m,"added_at","");r.source=Json.str(m,"source_name","");r.starred=Json.integer(m,"starred",0);r.size=objectPath(r.sha).length();
            List<String> tags=new ArrayList<String>();for(Object tag:Json.asList(m.get("tags")))tags.add((String)tag);r.tags=String.join("\u0001",tags);
            for(Object shelf:Json.asList(m.get("collections")))r.shelves.add((String)shelf);
            put(r);db.delete("trash","id=?",new String[]{id});db.setTransactionSuccessful();
        }finally{db.endTransaction();}
    }
    void purgeTrash(String id){
        String sha=null;db.beginTransaction();
        try{
            Cursor c=db.rawQuery("SELECT sha FROM trash WHERE id=?",new String[]{id});try{if(c.moveToFirst())sha=c.getString(0);}finally{c.close();}
            db.delete("trash","id=?",new String[]{id});db.setTransactionSuccessful();
        }finally{db.endTransaction();}
        if(sha!=null){Cursor count=db.rawQuery("SELECT (SELECT count(*) FROM files WHERE sha=?)+(SELECT count(*) FROM trash WHERE sha=?)",new String[]{sha,sha});try{if(count.moveToFirst()&&count.getInt(0)==0)objectPath(sha).delete();}finally{count.close();}}
    }
    void clearAll(){List<String> ids=new ArrayList<String>();for(Pack.Record r:all())ids.add(r.id);remove(ids);}

    long usedBytes() {
        Cursor cursor = db.rawQuery("SELECT IFNULL(SUM(size),0) FROM (SELECT sha,MAX(size) AS size FROM (SELECT sha,size FROM files UNION ALL SELECT sha,size FROM trash) GROUP BY sha)", null);
        try {
            return cursor.moveToFirst() ? cursor.getLong(0) : 0L;
        } finally {
            cursor.close();
        }
    }

    private static String cleanShelf(String name) {
        if (name == null) return "";
        String clean = name.trim().replaceAll("\\s+", " ");
        if (clean.length() > 40) clean = clean.substring(0, 40);
        return clean;
    }

    private static String escapeLike(String value) {
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    private static void deleteTree(File file) {
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteTree(child);
        file.delete();
    }
}
