"""Region history and local edit-state storage, separate from published images."""
from copy import deepcopy
from pathlib import Path
import hashlib, json, os, tempfile


class RegionHistory:
    def __init__(self, regions):
        self.regions = deepcopy(regions)
        self.undo_stack, self.redo_stack = [], []

    def commit(self, regions, before=None):
        before = deepcopy(self.regions if before is None else before)
        regions = deepcopy(regions)
        if regions == before:
            self.regions = regions
            return False
        self.undo_stack.append(before)
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear(); self.regions = regions
        return True

    def undo(self):
        if not self.undo_stack: return False
        self.redo_stack.append(deepcopy(self.regions)); self.regions = self.undo_stack.pop()
        return True

    def redo(self):
        if not self.redo_stack: return False
        self.undo_stack.append(deepcopy(self.regions)); self.regions = self.redo_stack.pop()
        return True


class EditStore:
    def __init__(self, path):
        self.path = Path(path)
        self.warning = ''
        try:
            self.records = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            if not isinstance(self.records, dict): raise ValueError('Invalid edit state.')
        except (OSError, ValueError):
            self.records = {}; self.warning = '編集状態を読み込めませんでした。自動検出から開始します。'

    @staticmethod
    def key(source):
        return os.path.normcase(str(Path(source).resolve()))

    def restore(self, source):
        record = self.records.get(self.key(source))
        if not isinstance(record, dict): return None
        if record.get('source_digest') != hashlib.sha256(Path(source).read_bytes()).hexdigest(): return None
        return deepcopy(record.get('detections'))

    def save(self, entry):
        updated = deepcopy(self.records)
        updated[self.key(entry['source'])] = {k: deepcopy(entry[k]) for k in ('source_digest', 'detections')}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.edits-', suffix='.json', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(updated, stream, ensure_ascii=False, indent=2)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self.records = updated


class RegionEditor:
    """Editing controls mixed into the existing comparison/zoom viewer."""
    def attach_editor(self):
        import tkinter as tk
        from tkinter import ttk
        self.selected_id = None
        self.drag_state = None
        self.preview_dirty = False
        self.close_after_save = False
        self.tool = tk.StringVar(value='select')
        self.edit_note = tk.StringVar(value='領域を選択して調整できます。保存するまで出力は変更しません。')
        ttk.Label(self.panel, text='モザイク領域の編集', font=('Meiryo', 11, 'bold')).pack(anchor='w')
        tools = ttk.Frame(self.panel); tools.pack(fill='x', pady=8)
        ttk.Radiobutton(tools, text='選択・移動', variable=self.tool, value='select').pack(anchor='w')
        ttk.Radiobutton(tools, text='領域を追加（ドラッグ）', variable=self.tool, value='add').pack(anchor='w')
        ttk.Label(self.panel, text='修正画像上で操作します。選択した枠の角をドラッグするとサイズを変更できます。', wraplength=230).pack(anchor='w')
        self.region_list = tk.Listbox(self.panel, height=5, exportselection=False)
        self.region_list.pack(fill='x', pady=8)
        self.region_list.bind('<<ListboxSelect>>', self.select_from_list)
        self.fields = {key: tk.StringVar(value='0') for key in ('x', 'y', 'width', 'height')}
        self.fields['block'] = tk.StringVar(value=str(self.app.settings['block']))
        fields = ttk.Frame(self.panel); fields.pack(fill='x')
        for key, label, row, column in (('x', 'X', 0, 0), ('y', 'Y', 0, 2), ('width', '幅', 1, 0), ('height', '高さ', 1, 2)):
            ttk.Label(fields, text=label).grid(row=row, column=column, sticky='w', pady=2)
            ttk.Entry(fields, textvariable=self.fields[key], width=6).grid(row=row, column=column+1, padx=4, pady=2)
        strength = ttk.Frame(self.panel); strength.pack(fill='x', pady=4)
        ttk.Label(strength, text='ブロック幅（px）').pack(side='left')
        ttk.Entry(strength, textvariable=self.fields['block'], width=6).pack(side='left', padx=6)
        actions = ttk.Frame(self.panel); actions.pack(fill='x', pady=4)
        ttk.Button(actions, text='選択領域へ適用', command=self.apply_fields).pack(side='left', expand=True, fill='x')
        ttk.Button(actions, text='領域を削除', command=self.delete_region).pack(side='left', expand=True, fill='x')
        history = ttk.Frame(self.panel); history.pack(fill='x', pady=6)
        ttk.Button(history, text='Undo', command=self.undo).pack(side='left', expand=True, fill='x')
        ttk.Button(history, text='Redo', command=self.redo).pack(side='left', expand=True, fill='x')
        ttk.Button(self.panel, text='リセット（全領域を消す）', command=self.reset_regions).pack(fill='x')
        self.save_button = ttk.Button(self.panel, text='この画像を保存・更新', command=self.save_regions)
        self.save_button.pack(fill='x', pady=12)
        ttk.Label(self.panel, textvariable=self.edit_note, wraplength=230).pack(anchor='w')
        self.canvas.bind('<ButtonPress-1>', self.pointer_down)
        self.canvas.bind('<B1-Motion>', self.pointer_move)
        self.canvas.bind('<ButtonRelease-1>', self.pointer_up)
        self.window.bind('<Control-z>', lambda e: self.undo())
        self.window.bind('<Control-y>', lambda e: self.redo())
        self.window.bind('<Control-Shift-Z>', lambda e: self.redo())
        self.window.bind('<Control-Shift-z>', lambda e: self.redo())
        self.refresh_regions()

    @property
    def regions(self):
        return self.history.regions

    def selected_region(self):
        return next((r for r in self.regions if r['id'] == self.selected_id), None)

    def refresh_regions(self):
        self.region_list.delete(0, 'end')
        for i, region in enumerate(self.regions):
            self.region_list.insert('end', f'領域 {i+1} ／ {region["block"]}px')
            if region['id'] == self.selected_id:
                self.region_list.selection_set(i)
        self.refresh_fields()

    def refresh_fields(self):
        region = self.selected_region()
        if region:
            x0, y0, x1, y1 = region['box']
            for key, value in zip(('x', 'y', 'width', 'height', 'block'), (x0, y0, x1-x0, y1-y0, region['block'])):
                self.fields[key].set(str(value))

    def select_from_list(self, event=None):
        selection = self.region_list.curselection()
        if selection and selection[0] < len(self.regions):
            self.selected_id = self.regions[selection[0]]['id']
            self.refresh_fields(); self.schedule_render()

    def edited(self):
        self.preview_dirty = True
        self.unsaved = self.regions != self.saved_regions
        self.edit_note.set('未保存の編集があります。「この画像を保存・更新」で反映します。' if self.unsaved else '編集状態は保存済みです。')
        self.refresh_regions(); self.schedule_render()

    def apply_fields(self):
        import batch
        from tkinter import messagebox
        if self.app.busy or not self.selected_region(): return
        try:
            x, y, width, height, block = (int(self.fields[k].get()) for k in ('x', 'y', 'width', 'height', 'block'))
            if width < 1 or height < 1 or block < 2:
                raise ValueError('幅・高さは1以上、ブロック幅は2以上にしてください。')
            box = list(batch.expand_region((x, y, x+width, y+height), 0, *self.source_image.size))
            regions = deepcopy(self.regions)
            region = next(r for r in regions if r['id'] == self.selected_id)
            region['box'] = box; region['block'] = block
            self.history.commit(regions); self.edited()
        except ValueError as exc:
            messagebox.showerror('領域の設定', str(exc), parent=self.window)

    def delete_region(self):
        if self.app.busy or not self.selected_region(): return
        self.history.commit([r for r in self.regions if r['id'] != self.selected_id])
        self.selected_id = None; self.edited()

    def reset_regions(self):
        if self.app.busy: return
        self.history.commit([]); self.selected_id = None; self.edited()

    def undo(self):
        if not self.app.busy and self.history.undo(): self.selected_id = None; self.edited()
        return 'break'

    def redo(self):
        if not self.app.busy and self.history.redo(): self.selected_id = None; self.edited()
        return 'break'

    def image_point(self, event):
        placement = self.placements.get('modified')
        if not placement: return None
        x, y, factor = placement
        px = (self.canvas.canvasx(event.x)-x)/factor
        py = (self.canvas.canvasy(event.y)-y)/factor
        if 0 <= px <= self.source_image.width and 0 <= py <= self.source_image.height:
            return round(px), round(py)
        return None

    def pointer_down(self, event):
        import uuid
        if self.app.busy: return
        point = self.image_point(event)
        if point is None: return
        before = deepcopy(self.regions)
        if self.tool.get() == 'add':
            try: block = max(2, int(self.fields['block'].get()))
            except ValueError: block = self.app.settings['block']
            x, y = point
            x = min(x, self.source_image.width-1); y = min(y, self.source_image.height-1)
            region = dict(id=uuid.uuid4().hex, box=[x, y, x+1, y+1], block=block, label='manual')
            self.history.regions.append(region); self.selected_id = region['id']
            self.drag_state = dict(before=before, start=(x, y), operation='add', corner=2)
        else:
            region = self.selected_region()
            corner = None
            tolerance = 8/self.placements['modified'][2]
            if region:
                x0, y0, x1, y1 = region['box']
                for i, (x, y) in enumerate(((x0,y0), (x1,y0), (x1,y1), (x0,y1))):
                    if abs(point[0]-x) <= tolerance and abs(point[1]-y) <= tolerance:
                        corner = i; break
            if corner is None:
                region = next((r for r in reversed(self.regions) if r['box'][0] <= point[0] <= r['box'][2] and r['box'][1] <= point[1] <= r['box'][3]), None)
            self.selected_id = region['id'] if region else None
            if region:
                self.drag_state = dict(before=before, start=point, operation='resize' if corner is not None else 'move', corner=corner, box=region['box'][:])
        self.refresh_regions(); self.schedule_render()

    def pointer_move(self, event):
        if self.app.busy or not self.drag_state: return
        point = self.image_point(event)
        if point is None:
            x, y, factor = self.placements['modified']
            point = (round(max(0, min(self.source_image.width, (self.canvas.canvasx(event.x)-x)/factor))), round(max(0, min(self.source_image.height, (self.canvas.canvasy(event.y)-y)/factor))))
        state, region = self.drag_state, self.selected_region()
        if region is None: return
        px, py = point
        if state['operation'] == 'add':
            sx, sy = state['start']
            x0, x1 = sorted((sx, px)); y0, y1 = sorted((sy, py))
            region['box'] = [x0, y0, min(self.source_image.width, max(x0+1,x1)), min(self.source_image.height, max(y0+1,y1))]
        elif state['operation'] == 'move':
            x0, y0, x1, y1 = state['box']
            dx = min(self.source_image.width-x1, max(-x0, px-state['start'][0]))
            dy = min(self.source_image.height-y1, max(-y0, py-state['start'][1]))
            region['box'] = [x0+dx, y0+dy, x1+dx, y1+dy]
        else:
            x0, y0, x1, y1 = state['box']; corner = state['corner']
            if corner in (0,3): x0 = min(x1-1, px)
            else: x1 = max(x0+1, px)
            if corner in (0,1): y0 = min(y1-1, py)
            else: y1 = max(y0+1, py)
            region['box'] = [x0, y0, x1, y1]
        self.preview_dirty = True; self.refresh_fields(); self.schedule_render()

    def pointer_up(self, event):
        if self.app.busy or not self.drag_state: return
        self.pointer_move(event)
        before = self.drag_state['before']; self.drag_state = None
        self.history.commit(self.regions, before=before); self.edited()

    def draw_regions(self):
        placement = self.placements.get('modified')
        if not placement: return
        x, y, scale = placement
        for index, region in enumerate(self.regions):
            x0, y0, x1, y1 = region['box']
            points = [x+x0*scale, y+y0*scale, x+x1*scale, y+y1*scale]
            selected = region['id'] == self.selected_id
            color = '#ffd166' if selected else '#ff4050'
            self.canvas.create_rectangle(*points, outline=color, width=2)
            self.canvas.create_text(points[0]+3, points[1]+3, text=str(index+1), fill=color, anchor='nw')
            if selected:
                for hx, hy in ((points[0],points[1]), (points[2],points[1]), (points[2],points[3]), (points[0],points[3])):
                    self.canvas.create_rectangle(hx-4, hy-4, hx+4, hy+4, fill=color, outline='#20242b')

    def save_regions(self):
        if self.app.busy: return
        self.app.save_document(self.index, deepcopy(self.regions))

    def mark_saved(self, entry):
        self.saved_regions = deepcopy(entry['detections'])
        self.history.regions = deepcopy(entry['detections'])
        self.unsaved = False
        self.preview_dirty = True
        self.schedule_render()
        self.edit_note.set('この画像の出力を更新しました。')
        self.save_button.configure(state='normal')
        if self.close_after_save: self.close()
