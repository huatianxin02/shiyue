package local.shiyue.vault;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.ActionMode;
import android.view.Menu;
import android.view.MenuItem;
import java.util.HashSet;
import java.util.Set;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * The phone side of 拾页: bring in a pack the Mac app exported, read it, file it.
 *
 * There is no network permission in the manifest, so "your files never leave the
 * phone" is a property of the build rather than a promise in a settings screen.
 */
public final class MainActivity extends Activity implements FileAdapter.Listener {

    private static final int REQ_IMPORT = 101;
    private static final int REQ_EXPORT = 102;

    private Library library;
    private final Set<String> selectedIds=new HashSet<String>();
    private ActionMode selectionMode;
    private List<String> exportIds=new ArrayList<String>();
    private final List<Pack.Record> rows = new ArrayList<Pack.Record>();
    private FileAdapter adapter;

    private TextView subtitle;
    private TextView empty;
    private TextView importButton;
    private LinearLayout kindRow;
    private LinearLayout shelfRow;
    private ListView list;
    private EditText search;
    private TextView clearSearch;

    private String kindFilter = "";
    private long shelfFilter = 0;
    private String searchText = "";

    private AlertDialog progress;
    private ProgressBar progressBar;
    private TextView progressText;
    private volatile boolean cancelled;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        library = new Library(this);
        if(savedInstanceState!=null){ArrayList<String> pending=savedInstanceState.getStringArrayList("exportIds");if(pending!=null)exportIds=pending;}
        setContentView(buildUi());
        refresh();
        openFromIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        openFromIntent(intent);
    }

    private void openFromIntent(Intent intent) {
        if (intent == null) return;
        if (Intent.ACTION_VIEW.equals(intent.getAction()) && intent.getData() != null) {
            startImport(intent.getData());
        }
    }

    // ------------------------------------------------------------------- UI

    private View buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Ui.PAPER);
        // targetSdk 36 draws edge to edge by default; keep content clear of the bars.
        root.setFitsSystemWindows(true);

        root.addView(buildHeader());

        LinearLayout searchBar = new LinearLayout(this);
        searchBar.setOrientation(LinearLayout.HORIZONTAL);
        searchBar.setGravity(Gravity.CENTER_VERTICAL);
        searchBar.setBackground(Ui.round(this, 0xFFFFFFFF, 14));
        searchBar.setPadding(Ui.dp(this, 14), Ui.dp(this, 2), Ui.dp(this, 14), Ui.dp(this, 2));
        LinearLayout.LayoutParams searchParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, Ui.dp(this, 46));
        searchParams.setMargins(Ui.dp(this, 20), Ui.dp(this, 4), Ui.dp(this, 20), 0);
        root.addView(searchBar, searchParams);

        TextView glass = Ui.iconButton(this, "search", "搜索");
        glass.setMinWidth(Ui.dp(this, 30));
        glass.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        glass.setPadding(0, 0, Ui.dp(this, 8), Ui.dp(this, 2));
        searchBar.addView(glass);

        search = new EditText(this);
        search.setHint("搜索名称或标签");
        search.setHintTextColor(Ui.MUTED);
        search.setTextColor(Ui.INK);
        search.setTextSize(android.util.TypedValue.COMPLEX_UNIT_SP, 14f);
        search.setSingleLine(true);
        search.setContentDescription("搜索名称或标签");
        search.setBackground(null);
        search.setPadding(0, 0, 0, 0);
        searchBar.addView(search, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        search.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            public void onTextChanged(CharSequence s, int a, int b, int c) {}
            public void afterTextChanged(Editable s) {
                searchText = s.toString();
                clearSearch.setVisibility(searchText.length() == 0 ? View.GONE : View.VISIBLE);
                refresh();
            }
        });

        clearSearch = Ui.iconButton(this, "close", "清除搜索");
        clearSearch.setVisibility(View.GONE);
        clearSearch.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { search.setText(""); search.requestFocus(); }
        });
        searchBar.addView(clearSearch);

        HorizontalScrollView kindScroll = new HorizontalScrollView(this);
        kindScroll.setHorizontalScrollBarEnabled(false);
        kindScroll.setClipToPadding(false);
        kindScroll.setPadding(Ui.dp(this, 20), 0, Ui.dp(this, 20), 0);
        LinearLayout.LayoutParams kindParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        kindParams.topMargin = Ui.dp(this, 12);
        root.addView(kindScroll, kindParams);
        kindRow = new LinearLayout(this);
        kindRow.setOrientation(LinearLayout.HORIZONTAL);
        kindScroll.addView(kindRow, new HorizontalScrollView.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        HorizontalScrollView shelfScroll = new HorizontalScrollView(this);
        shelfScroll.setHorizontalScrollBarEnabled(false);
        shelfScroll.setClipToPadding(false);
        shelfScroll.setPadding(Ui.dp(this, 20), 0, Ui.dp(this, 20), 0);
        LinearLayout.LayoutParams shelfParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        shelfParams.topMargin = Ui.dp(this, 8);
        root.addView(shelfScroll, shelfParams);
        shelfRow = new LinearLayout(this);
        shelfRow.setOrientation(LinearLayout.HORIZONTAL);
        shelfScroll.addView(shelfRow, new HorizontalScrollView.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams bodyParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        bodyParams.topMargin = Ui.dp(this, 10);
        root.addView(body, bodyParams);

        list = new ListView(this);
        list.setDivider(null);
        list.setDividerHeight(0);
        list.setSelector(new android.graphics.drawable.ColorDrawable(0x00000000));
        list.setPadding(Ui.dp(this, 20), 0, Ui.dp(this, 20), Ui.dp(this, 16));
        list.setClipToPadding(false);
        list.setVerticalScrollBarEnabled(false);
        adapter = new FileAdapter(this, this);
        list.setAdapter(adapter);
        body.addView(list, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        empty = Ui.text(this, "还没有内容\n\n点右上角「导入」，选择 Mac 导出的资料包", 14f, Ui.MUTED);
        empty.setGravity(Gravity.CENTER);
        empty.setLineSpacing(Ui.dp(this, 5), 1f);
        empty.setVisibility(View.GONE);
        empty.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                if (library.counts()[0] == 0) { pickPack(); return; }
                kindFilter = ""; shelfFilter = 0; searchText = ""; search.setText(""); refresh();
            }
        });
        body.addView(empty, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        return root;
    }

    private View buildHeader() {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(Ui.dp(this, 20), Ui.dp(this, 18), Ui.dp(this, 18), Ui.dp(this, 8));

        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        header.addView(titles, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        TextView title = Ui.bold(Ui.text(this, "拾页", 25f, Ui.INK));
        title.setLetterSpacing(0.06f);
        titles.addView(title);

        subtitle = Ui.text(this, "", 11.5f, Ui.MUTED);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        subtitleParams.topMargin = Ui.dp(this, 2);
        titles.addView(subtitle, subtitleParams);

        importButton = Ui.text(this, "导入", 13.5f, 0xFFFFFFFF);
        importButton.setTypeface(Typeface.DEFAULT_BOLD);
        importButton.setGravity(Gravity.CENTER);
        importButton.setPadding(Ui.dp(this, 18), Ui.dp(this, 9), Ui.dp(this, 18), Ui.dp(this, 9));
        importButton.setBackground(Ui.round(this, Ui.TEAL, 8));
        importButton.setMinHeight(Ui.dp(this, 44));
        importButton.setContentDescription("导入资料包");
        importButton.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { pickPack(); }
        });
        header.addView(importButton);

        TextView more = Ui.iconButton(this, "more", "更多操作");
        more.setGravity(Gravity.CENTER);
        more.setPadding(Ui.dp(this, 12), Ui.dp(this, 6), Ui.dp(this, 2), Ui.dp(this, 6));
        more.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { showMore(v); }
        });
        header.addView(more);

        return header;
    }

    private void showMore(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        menu.getMenu().add(0, 1, 0, "导出资料包");
        menu.getMenu().add(0, 4, 1, "回收站");
        menu.getMenu().add(0, 2, 2, "将全部资料移入回收站");
        menu.getMenu().add(0, 3, 2, "关于拾页");
        menu.setOnMenuItemClickListener(new PopupMenu.OnMenuItemClickListener() {
            public boolean onMenuItemClick(android.view.MenuItem item) {
                if (item.getItemId() == 1) exportPack();
                else if (item.getItemId() == 2) confirmClear();
                else if(item.getItemId()==4)showTrash();
                else about();
                return true;
            }
        });
        menu.show();
    }

    // -------------------------------------------------------------- refresh

    private void refresh() {
        rows.clear();
        rows.addAll(library.query(kindFilter, shelfFilter, searchText));
        adapter.setRows(rows);

        int[] counts = library.counts();
        long bytes = library.usedBytes();
        int shelves = library.shelves().size();
        subtitle.setText(counts[0] + " 个文件 · " + FileAdapter.readableSize(bytes)
                + (shelves > 0 ? " · " + shelves + " 个分类" : ""));

        boolean blank = rows.isEmpty();
        empty.setVisibility(blank ? View.VISIBLE : View.GONE);
        list.setVisibility(blank ? View.GONE : View.VISIBLE);
        if (blank && counts[0] > 0) {
            empty.setText("没有符合条件的资料\n\n点击清除搜索与筛选");
        } else if (blank) {
            empty.setText("还没有内容\n\n点右上角「导入」，选择 Mac 导出的资料包");
        }

        renderKindChips(counts);
        renderShelfChips();
    }

    private void renderKindChips(int[] counts) {
        kindRow.removeAllViews();
        addChip(kindRow, "全部 " + counts[0], kindFilter.length() == 0, new View.OnClickListener() {
            public void onClick(View v) {
                kindFilter = "";
                refresh();
            }
        });
        String[] kinds = {"document", "image", "video"};
        String[] labels = {"文档", "图片", "视频"};
        int[] totals = {counts[1], counts[2], counts[3]};
        for (int i = 0; i < kinds.length; i++) {
            final String kind = kinds[i];
            addChip(kindRow, labels[i] + " " + totals[i], kind.equals(kindFilter), new View.OnClickListener() {
                public void onClick(View v) {
                    kindFilter = kind;
                    refresh();
                }
            });
        }
    }

    private void renderShelfChips() {
        shelfRow.removeAllViews();
        addChip(shelfRow, "全部分类", shelfFilter == 0, new View.OnClickListener() {
            public void onClick(View v) {
                shelfFilter = 0;
                refresh();
            }
        });
        for (final Library.Shelf shelf : library.shelves()) {
            TextView chip = addChip(shelfRow, shelf.name + " " + shelf.count, shelfFilter == shelf.id,
                    new View.OnClickListener() {
                        public void onClick(View v) {
                            shelfFilter = shelf.id;
                            refresh();
                        }
                    });
            chip.setOnLongClickListener(new View.OnLongClickListener() {
                public boolean onLongClick(View v) {
                    shelfActions(shelf);
                    return true;
                }
            });
        }
        TextView create = addChip(shelfRow, "＋ 新建", false, new View.OnClickListener() {
            public void onClick(View v) { promptNewShelf(null); }
        });
        create.setTextColor(Ui.TEAL);
    }

    private TextView addChip(LinearLayout row, String label, boolean selected, View.OnClickListener click) {
        TextView chip = Ui.chip(this, label, selected, Ui.TEAL, 0xFFFFFFFF, Ui.INK, Ui.MUTED);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.rightMargin = Ui.dp(this, 8);
        chip.setLayoutParams(params);
        chip.setOnClickListener(click);
        row.addView(chip);
        return chip;
    }

    // -------------------------------------------------------------- actions

    public void onOpen(Pack.Record record) {
        if(selectionMode!=null){toggleSelection(record.id);return;}
        // These notes are laid out for a desktop column, so the browser is the better
        // reader; if nothing on the phone claims HTML, fall back to the in-app view.
        if (OpenWith.isHtml(record.name) && OpenWith.openInBrowser(this, record)) return;
        ReaderActivity.open(this, record.id);
    }

    public void onActions(final Pack.Record record) {
        if(selectionMode!=null){toggleSelection(record.id);return;}
        String[] actions = {"打开方式…", "加入分类…", "新建分类并加入…", record.starred!=0?"取消收藏":"收藏", "编辑标签…", "选择多份…", "从资料库移除"};
        new AlertDialog.Builder(this)
                .setTitle(record.name)
                .setItems(actions, new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        if (which == 0) OpenWith.show(MainActivity.this, record, false);
                        else if (which == 1) pickShelves(record);
                        else if (which == 2) promptNewShelf(record);
                        else if(which==3){library.edit(record.id,null,record.starred==0);refresh();}
                        else if(which==4)editTags(record);
                        else if(which==5)beginSelection(record.id);
                        else confirmRemove(record);
                    }
                })
                .show();
    }

    private void editTags(final Pack.Record record) {
        final EditText input=new EditText(this);input.setHint("用逗号分隔标签");input.setText(record.tags.replace('\u0001','，'));
        new AlertDialog.Builder(this).setTitle("编辑标签").setView(input).setNegativeButton("取消",null).setPositiveButton("保存",new android.content.DialogInterface.OnClickListener(){ public void onClick(android.content.DialogInterface d,int w){
            String[] parts=input.getText().toString().split("[,，\\n]");
            if(parts.length>50){toast("最多 50 个标签");return;}
            java.util.LinkedHashSet<String> tags=new java.util.LinkedHashSet<String>();
            for(String part:parts){part=part.trim();if(part.length()>80){toast("单个标签最多 80 个字");return;}if(!part.isEmpty())tags.add(part);}
            library.edit(record.id,String.join("\u0001",tags),null);refresh();
        }}).show();
    }
    private void beginSelection(String id){
        selectionMode=startActionMode(new ActionMode.Callback(){
            public boolean onCreateActionMode(ActionMode mode,Menu menu){menu.add(0,1,0,"加入分类");menu.add(0,2,1,"导出所选");menu.add(0,3,2,"收藏");return true;}
            public boolean onPrepareActionMode(ActionMode mode,Menu menu){return false;}
            public void onDestroyActionMode(ActionMode mode){selectionMode=null;selectedIds.clear();adapter.setSelection(selectedIds);}
            public boolean onActionItemClicked(final ActionMode mode,MenuItem item){
                if(selectedIds.isEmpty()){toast("请先选择资料");return true;}
                final List<String> ids=new ArrayList<String>(selectedIds);
                if(item.getItemId()==1){
                    final List<Library.Shelf> shelves=library.shelves();
                    if(shelves.isEmpty()){toast("请先新建分类");return true;}
                    String[] names=new String[shelves.size()];for(int i=0;i<names.length;i++)names[i]=shelves.get(i).name;
                    new AlertDialog.Builder(MainActivity.this).setTitle("将所选资料加入分类").setItems(names,new android.content.DialogInterface.OnClickListener(){public void onClick(android.content.DialogInterface d,int index){library.assign(shelves.get(index).id,ids);mode.finish();refresh();}}).show();
                }else if(item.getItemId()==2){exportPack(ids);}
                else {for(String fid:ids)library.edit(fid,null,true);mode.finish();refresh();}
                return true;
            }
        });
        toggleSelection(id);
    }
    private void toggleSelection(String id){if(!selectedIds.add(id))selectedIds.remove(id);adapter.setSelection(selectedIds);if(selectionMode!=null)selectionMode.setTitle("已选择 "+selectedIds.size()+" 份");}
    @Override protected void onSaveInstanceState(Bundle state){super.onSaveInstanceState(state);state.putStringArrayList("exportIds",new ArrayList<String>(exportIds));}

    private void pickShelves(final Pack.Record record) {
        final List<Library.Shelf> shelves = library.shelves();
        if (shelves.isEmpty()) {
            promptNewShelf(record);
            return;
        }
        final String[] labels = new String[shelves.size()];
        final boolean[] checked = new boolean[shelves.size()];
        for (int i = 0; i < shelves.size(); i++) {
            Library.Shelf shelf = shelves.get(i);
            labels[i] = shelf.name;
            checked[i] = record.shelves.contains(shelf.name);
        }
        new AlertDialog.Builder(this)
                .setTitle("归类")
                .setMultiChoiceItems(labels, checked, new android.content.DialogInterface.OnMultiChoiceClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which, boolean isChecked) {
                        checked[which] = isChecked;
                    }
                })
                .setPositiveButton("完成", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        List<String> ids = new ArrayList<String>();
                        ids.add(record.id);
                        for (int i = 0; i < shelves.size(); i++) {
                            Library.Shelf shelf = shelves.get(i);
                            boolean was = record.shelves.contains(shelf.name);
                            if (checked[i] && !was) library.assign(shelf.id, ids);
                            if (!checked[i] && was) library.unassign(shelf.id, ids);
                        }
                        refresh();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void promptNewShelf(final Pack.Record record) {
        final EditText input = new EditText(this);
        input.setHint("例如：AI资料");
        input.setSingleLine(true);
        input.setPadding(Ui.dp(this, 22), Ui.dp(this, 8), Ui.dp(this, 22), Ui.dp(this, 8));
        new AlertDialog.Builder(this)
                .setTitle("新建分类")
                .setView(input)
                .setPositiveButton("创建", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        String name = input.getText().toString().trim();
                        if (name.length() == 0) {
                            toast("分类名称不能为空");
                            return;
                        }
                        long id = library.createShelf(name);
                        if (id <= 0) {
                            toast("这个分类已经存在");
                            return;
                        }
                        if (record != null) {
                            List<String> ids = new ArrayList<String>();
                            ids.add(record.id);
                            library.assign(id, ids);
                        }
                        shelfFilter = id;
                        refresh();
                        toast("已创建分类「" + name + "」");
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void shelfActions(final Library.Shelf shelf) {
        String[] actions = {"重命名", "删除分类"};
        new AlertDialog.Builder(this)
                .setTitle(shelf.name + " · " + shelf.count + " 个文件")
                .setItems(actions, new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        if (which == 0) renameShelf(shelf);
                        else confirmDeleteShelf(shelf);
                    }
                })
                .show();
    }

    private void renameShelf(final Library.Shelf shelf) {
        final EditText input = new EditText(this);
        input.setText(shelf.name);
        input.setSingleLine(true);
        input.setPadding(Ui.dp(this, 22), Ui.dp(this, 8), Ui.dp(this, 22), Ui.dp(this, 8));
        new AlertDialog.Builder(this)
                .setTitle("重命名分类")
                .setView(input)
                .setPositiveButton("保存", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        if (library.renameShelf(shelf.id, input.getText().toString())) {
                            refresh();
                        } else {
                            toast("名称无效或已存在");
                        }
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void confirmDeleteShelf(final Library.Shelf shelf) {
        new AlertDialog.Builder(this)
                .setTitle("删除分类「" + shelf.name + "」？")
                .setMessage("分类里的 " + shelf.count + " 个文件仍会留在资料库中。")
                .setPositiveButton("删除", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        library.deleteShelf(shelf.id);
                        if (shelfFilter == shelf.id) shelfFilter = 0;
                        refresh();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void confirmRemove(final Pack.Record record) {
        new AlertDialog.Builder(this)
                .setTitle("从资料库移除？")
                .setMessage("「" + record.name + "」会移入手机回收站，内容和分类可以恢复。Mac 上的原件不受影响。")
                .setPositiveButton("移除", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        List<String> ids = new ArrayList<String>();
                        ids.add(record.id);
                        library.remove(ids);
                        refresh();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void showTrash(){
        final List<java.util.Map<String,Object>> rows=library.trashRows();
        if(rows.isEmpty()){toast("回收站为空");return;}
        String[] names=new String[rows.size()];for(int i=0;i<names.length;i++)names[i]=Json.str(Json.asMap(rows.get(i).get("record")),"name","");
        new AlertDialog.Builder(this).setTitle("回收站").setItems(names,new android.content.DialogInterface.OnClickListener(){public void onClick(android.content.DialogInterface dialog,int which){
            final String id=(String)rows.get(which).get("id");
            new AlertDialog.Builder(MainActivity.this).setTitle("恢复或永久清理").setMessage("恢复会保留本机已有编辑并合并分类；永久清理无法撤销。").setPositiveButton("恢复",new android.content.DialogInterface.OnClickListener(){public void onClick(android.content.DialogInterface d,int w){library.restoreTrash(id);refresh();toast("已恢复");}}).setNegativeButton("取消",null).setNeutralButton("永久清理",new android.content.DialogInterface.OnClickListener(){public void onClick(android.content.DialogInterface d,int w){
                new AlertDialog.Builder(MainActivity.this).setTitle("永久清理？").setMessage("不再被引用的内容副本将永久删除，无法撤销。请先确认已有其他备份。").setNegativeButton("取消",null).setPositiveButton("永久清理",new android.content.DialogInterface.OnClickListener(){public void onClick(android.content.DialogInterface x,int y){library.purgeTrash(id);refresh();toast("已永久清理");}}).show();
            }}).show();
        }}).setNegativeButton("关闭",null).show();
    }

    private void confirmClear() {
        new AlertDialog.Builder(this)
                .setTitle("全部移入回收站？")
                .setMessage("全部资料会移入手机回收站，分类保留。之后可恢复；永久清理需要单独确认。")
                .setPositiveButton("移入回收站", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        library.clearAll();
                        kindFilter = "";
                        shelfFilter = 0;
                        search.setText("");
                        refresh();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void about() {
        String version = "1.0.0";
        try {
            version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception ignored) {
        }
        new AlertDialog.Builder(this)
                .setTitle("拾页 · 手机阅读")
                .setMessage("版本 " + version + "\n\n"
                        + "导入 Mac 端导出的 .vaultpack 资料包，在手机上进行阅读与分类；"
                        + "也可以把手机上的分类重新打包带回电脑。\n\n"
                        + "应用没有联网权限，所有内容只保存在这台手机上。")
                .setPositiveButton("知道了", null)
                .show();
    }

    // --------------------------------------------------------------- import

    private void pickPack() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        try {
            startActivityForResult(intent, REQ_IMPORT);
        } catch (ActivityNotFoundException e) {
            toast("这台设备没有可用的文件选择器");
        }
    }

    private void startImport(final Uri uri) {
        if(progress!=null&&progress.isShowing()){toast("已有任务运行，请等待完成后再导入");return;}
        showProgress("导入资料包", true);
        new Thread(new Runnable() {
            public void run() {
                File incoming = new File(getCacheDir(), "incoming.vaultpack");
                try {
                    copyUri(uri, incoming);
                    runOnUiThread(new Runnable() {
                        public void run() { setProgressText("正在校验内容…重复内容将保留本机编辑并合并分类。"); }
                    });
                    Pack.Summary summary = Pack.importPack(incoming, library, progress());
                    incoming.delete();
                    final String message = "已导入 " + summary.files + " 个文件"
                            + (summary.shelves.isEmpty() ? "" : " · " + summary.shelves.size() + " 个分类");
                    runOnUiThread(new Runnable() {
                        public void run() {
                            dismissProgress();
                            kindFilter = "";
                            shelfFilter = 0;
                            search.setText("");
                            refresh();
                            toast(message);
                        }
                    });
                } catch (final Exception e) {
                    incoming.delete();
                    runOnUiThread(new Runnable() {
                        public void run() {
                            dismissProgress();
                            failure("导入没有完成", e);
                        }
                    });
                }
            }
        }).start();
    }

    private void exportPack() { exportPack(new ArrayList<String>()); }
    private void exportPack(List<String> ids) {
        exportIds=new ArrayList<String>(ids);
        if (library.counts()[0] == 0) {
            toast("资料库是空的，没有可以导出的内容");
            return;
        }
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmm", Locale.CHINA).format(new Date());
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        // A vaultpack is a zip, but declaring it as application/zip makes the system
        // save dialog append ".zip" to the name. The app's own type keeps the name.
        intent.setType("application/vnd.shiyue.vaultpack");
        intent.putExtra(Intent.EXTRA_TITLE, "拾页资料包-" + stamp + ".vaultpack");
        try {
            startActivityForResult(intent, REQ_EXPORT);
        } catch (ActivityNotFoundException e) {
            toast("这台设备没有可用的文件保存器");
        }
    }

    private void startExport(final Uri uri) {
        if(progress!=null&&progress.isShowing()){toast("已有任务运行，请等待完成后再导出");return;}
        showProgress("导出资料包", true);
        new Thread(new Runnable() {
            public void run() {
                File outgoing = new File(getCacheDir(), "outgoing.vaultpack");
                try {
                    Pack.Summary summary = Pack.exportPack(outgoing, exportIds.isEmpty()?library:library.subset(exportIds), progress());
                    writeUri(uri, outgoing);
                    outgoing.delete();
                    final String message = "已导出 " + summary.files + " 个文件 · "
                            + FileAdapter.readableSize(summary.bytes);
                    runOnUiThread(new Runnable() {
                        public void run() {
                            dismissProgress();
                            toast(message);
                        }
                    });
                } catch (final Exception e) {
                    outgoing.delete();
                    runOnUiThread(new Runnable() {
                        public void run() {
                            dismissProgress();
                            failure("导出没有完成", e);
                        }
                    });
                }
            }
        }).start();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null || data.getData() == null) return;
        if (requestCode == REQ_IMPORT) startImport(data.getData());
        else if (requestCode == REQ_EXPORT) startExport(data.getData());
    }

    private void copyUri(Uri uri, File target) throws Exception {
        InputStream in = getContentResolver().openInputStream(uri);
        if (in == null) throw new Exception("无法读取所选文件");
        try {
            OutputStream out = new FileOutputStream(target);
            try {
                byte[] buffer = new byte[64 * 1024];
                int read;
                while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
            } finally {
                out.close();
            }
        } finally {
            in.close();
        }
    }

    private void writeUri(Uri uri, File source) throws Exception {
        OutputStream out = getContentResolver().openOutputStream(uri);
        if (out == null) throw new Exception("无法写入所选位置");
        InputStream in = new java.io.FileInputStream(source);
        try {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) out.write(buffer, 0, read);
        } finally {
            in.close();
            out.close();
        }
    }

    // ------------------------------------------------------------- progress

    private Pack.Progress progress() {
        return new Pack.Progress() {
            public void step(final int done, final int total, final String label) {
                runOnUiThread(new Runnable() {
                    public void run() {
                        if (progressBar == null) return;
                        progressBar.setIndeterminate(false);
                        progressBar.setMax(Math.max(total, 1));
                        progressBar.setProgress(done);
                        setProgressText(label + "（" + done + "/" + total + "）");
                    }
                });
            }

            public boolean cancelled() { return cancelled; }
        };
    }

    private void showProgress(String title, boolean indeterminate) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(Ui.dp(this, 22), Ui.dp(this, 18), Ui.dp(this, 22), Ui.dp(this, 6));

        progressText = Ui.text(this, "准备中…", 13f, Ui.MUTED);
        box.addView(progressText);

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setIndeterminate(indeterminate);
        LinearLayout.LayoutParams barParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        barParams.topMargin = Ui.dp(this, 12);
        box.addView(progressBar, barParams);

        cancelled = false;
        progress = new AlertDialog.Builder(this)
                .setTitle(title)
                .setView(box)
                .setNegativeButton("取消", new android.content.DialogInterface.OnClickListener() {
                    public void onClick(android.content.DialogInterface dialog, int which) {
                        cancelled = true;
                    }
                })
                .create();
        progress.setCanceledOnTouchOutside(false);
        progress.show();
    }

    private void setProgressText(final String text) {
        if (progressText != null) progressText.setText(text);
    }

    private void dismissProgress() {
        if (progress != null) {
            progress.dismiss();
            progress = null;
        }
        progressBar = null;
        progressText = null;
    }

    private void failure(String title, Exception error) {
        String message = error.getMessage();
        if (message == null || message.length() == 0) message = error.getClass().getSimpleName();
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setPositiveButton("知道了", null)
                .show();
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }
}
