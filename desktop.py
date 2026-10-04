"""Desktop entry point and local review viewer."""
import os, queue, threading, json, math
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES
from PIL import Image, ImageTk, ImageDraw
import batch
from copy import deepcopy
from editing import EditStore, RegionHistory, RegionEditor


def validate_settings(values):
    block=int(values['block']); padding=int(values['padding']); confidence=float(values['confidence'])
    if block<2: raise ValueError('モザイクのブロック幅は2px以上にしてください。')
    if padding<0: raise ValueError('余白は0px以上にしてください。')
    if not math.isfinite(confidence) or not 0<=confidence<=1: raise ValueError('検出しきい値は0〜1にしてください。')
    return dict(block=block,padding=padding,confidence=confidence,include_nipple=bool(values['include_nipple']),save_log=bool(values['save_log']),strip_metadata=bool(values.get('strip_metadata',True)))


class ImageViewer(RegionEditor):
    def __init__(self, app, index):
        self.app=app
        self.report=app.report
        self.index=index
        entry=self.report['images'][index]
        self.mode=tk.StringVar(value='modified')
        self.zoom=tk.StringVar(value='画面に合わせる')
        self.pending=None
        self.images={}
        self.photos=[]
        self.display_sizes=[]
        original=Path(entry.get('source') or Path(self.report['input'])/entry['file'])
        modified=Path(entry.get('output_path') or Path(self.report['output'])/entry['file'])
        self.source_image,_=batch.load_original(original)
        self.history=RegionHistory(batch.normalize_regions(entry.get('detections') or [], self.source_image.size, app.settings['block']))
        self.saved_regions=deepcopy(self.history.regions)
        self.unsaved=False
        for key,path in (('original',original),('modified',modified if modified.is_file() else original)):
            with Image.open(path) as image:
                self.images[key]=image.convert('RGBA')
        if 'source' in entry and entry['status']!='保存済み':
            self.images['modified']=batch.pixelate(self.source_image,self.history.regions,app.settings['block']).convert('RGBA')
        self.window=tk.Toplevel(app.root)
        self.window.title('画像確認 — '+entry['file'])
        self.window.geometry('1200x850')
        self.window.minsize(1000,680)
        self.window.transient(app.root)
        self.placements={}
        self.view_signature=None
        bar=ttk.Frame(self.window,padding=10); bar.pack(fill='x')
        ttk.Button(bar,text='← 一覧に戻る',command=self.close).pack(side='left')
        for text,value in (('修正画像だけ','modified'),('原画だけ','original'),('原画と修正画像を並べる','both')):
            ttk.Radiobutton(bar,text=text,value=value,variable=self.mode,command=self.schedule_render).pack(side='left',padx=8)
        info=ttk.Frame(self.window,padding=(10,0,10,8)); info.pack(fill='x')
        ttk.Label(info,text=entry['file']).pack(side='left')
        ttk.Label(info,text='表示倍率：').pack(side='left',padx=(20,0))
        self.zoom_box=ttk.Combobox(info,textvariable=self.zoom,values=('画面に合わせる','100%','200%','400%'),state='readonly',width=16)
        self.zoom_box.pack(side='left'); self.zoom_box.bind('<<ComboboxSelected>>',self.schedule_render)
        self.zoom_note=tk.StringVar()
        ttk.Label(info,textvariable=self.zoom_note).pack(side='left',padx=10)
        body=ttk.Frame(self.window); body.pack(fill='both',expand=True)
        self.panel=ttk.Frame(body,padding=12,width=250); self.panel.pack(side='right',fill='y')
        self.canvas=tk.Canvas(body,bg='#20242b',highlightthickness=0)
        ys=ttk.Scrollbar(body,orient='vertical',command=self.canvas.yview); ys.pack(side='right',fill='y')
        xs=ttk.Scrollbar(body,orient='horizontal',command=self.canvas.xview); xs.pack(side='bottom',fill='x')
        self.canvas.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',self.schedule_render)
        self.canvas.bind('<Map>',self.schedule_render)
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units'))
        self.canvas.bind('<Shift-MouseWheel>',lambda e:self.canvas.xview_scroll(-int(e.delta/120),'units'))
        self.attach_editor()
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Escape>',lambda e:self.close())
        self.window.bind('<F11>',lambda e:self.window.attributes('-fullscreen',not self.window.attributes('-fullscreen')))
        self.window.deiconify(); self.window.lift(); self.window.focus_set()
        # First render after mapping, then re-render after every size change.
        self.schedule_render()

    def schedule_render(self,*args):
        if self.pending is not None:
            self.window.after_cancel(self.pending)
        self.pending=self.window.after(30,self.render)

    def render(self):
        self.pending=None
        width,height=self.canvas.winfo_width(),self.canvas.winfo_height()
        if width<=2 or height<=2: return
        if self.preview_dirty:
            self.images['modified']=batch.pixelate(self.source_image,self.regions,self.app.settings['block']).convert('RGBA')
            self.preview_dirty=False
        keys=['original','modified'] if self.mode.get()=='both' else [self.mode.get()]
        gap=24; top=32
        source=self.images[keys[0]]
        if self.zoom.get()=='画面に合わせる':
            factor=min(max(1,(width-gap*(len(keys)+1))/len(keys))/source.width,max(1,height-top-16)/source.height)
        else:
            factor=float(self.zoom.get().rstrip('%'))/100
        self.canvas.delete('all'); self.photos.clear(); self.display_sizes.clear()
        display=(max(1,round(source.width*factor)),max(1,round(source.height*factor)))
        content_width=len(keys)*display[0]+gap*(len(keys)+1)
        offset=max(0,(width-content_width)//2)
        self.placements={}
        for i,key in enumerate(keys):
            shown=self.images[key].resize(display,Image.Resampling.LANCZOS)
            photo=ImageTk.PhotoImage(shown,master=self.window)
            self.photos.append(photo); self.display_sizes.append(display)
            x=offset+gap+i*(display[0]+gap)
            self.placements[key]=(x,top,factor)
            self.canvas.create_text(x,8,text='原画' if key=='original' else ('修正画像（編集中）' if self.unsaved else '修正画像'),anchor='nw',fill='white',font=('Meiryo',11,'bold'))
            self.canvas.create_image(x,top,image=photo,anchor='nw')
        self.canvas.configure(scrollregion=(0,0,max(width,content_width),max(height,display[1]+top+16)))
        self.draw_regions()
        signature=(keys,display,width,height)
        if signature!=self.view_signature:
            self.canvas.xview_moveto(0); self.canvas.yview_moveto(0)
            self.view_signature=signature
        self.zoom_note.set(f'{factor*100:.0f}% ／ 原寸 {source.width} × {source.height}px')

    def close(self):
        if self.app.busy: return False
        if self.unsaved:
            answer=messagebox.askyesnocancel('未保存の編集','この画像の編集を保存しますか？',parent=self.window)
            if answer is None: return False
            if answer:
                self.close_after_save=True; self.save_regions(); return False
        if self.pending is not None: self.window.after_cancel(self.pending)
        self.window.destroy(); self.app.viewer=None; self.app.image_viewer=None
        return True


class App:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.folder = None
        self.documents = []
        self.report = None
        self.output = Path(args.output).resolve() if args.output else None
        self.output_explicit = bool(args.output)
        self.busy = False
        self.events = queue.Queue()
        self.photos = []
        self.viewer = self.image_viewer = self.settings_window = None
        self.settings_path = batch.ROOT/'settings.json'
        self.settings = validate_settings(dict(block=args.block, padding=args.padding, confidence=args.confidence, include_nipple=args.include_nipple, save_log=getattr(args,'save_log',False), strip_metadata=getattr(args,'strip_metadata',True)))
        self.settings_warning = ''
        if self.settings_path.exists():
            try: self.settings = validate_settings(json.loads(self.settings_path.read_text(encoding='utf-8')))
            except (OSError,ValueError,KeyError,TypeError): self.settings_warning = '設定ファイルを読めなかったため、初期設定を使用しています。'
        self.edit_store = EditStore(batch.STATE_ROOT/'edits.json')
        if self.edit_store.warning: self.settings_warning += self.edit_store.warning
        root.title('MosaicDesk'); root.geometry('1100x820'); root.minsize(950,650)
        root.option_add('*Font', 'Meiryo 10')
        main=ttk.Frame(root,padding=18); main.pack(fill='both',expand=True)
        ttk.Label(main,text='MosaicDesk',font=('Meiryo',20,'bold')).pack(anchor='w')
        self.drop=ttk.Frame(main,padding=14,relief='ridge'); self.drop.pack(fill='x',pady=(12,8))
        drop_label=ttk.Label(self.drop,text='ここにPNG画像・フォルダをドラッグ＆ドロップ（複数可）',anchor='center')
        drop_label.pack(fill='x',pady=(0,10))
        choices=ttk.Frame(self.drop); choices.pack()
        self.choose_button=ttk.Button(choices,text='フォルダを選ぶ',command=self.choose); self.choose_button.pack(side='left',padx=5)
        self.add_button=ttk.Button(choices,text='画像を追加',command=self.choose_images); self.add_button.pack(side='left',padx=5)
        self.clear_button=ttk.Button(choices,text='一覧をクリア',command=self.clear_inputs); self.clear_button.pack(side='left',padx=5)
        for target in (root,self.drop,drop_label,choices,self.choose_button,self.add_button):
            target.drop_target_register(DND_FILES); target.dnd_bind('<<Drop>>',self.on_drop)
        self.path_text=tk.StringVar(value='PNG画像またはフォルダを追加してください。')
        ttk.Label(main,textvariable=self.path_text,wraplength=1020).pack(anchor='w')
        controls=ttk.Frame(main); controls.pack(fill='x',pady=10)
        self.start_button=ttk.Button(controls,text='未処理の画像を一括処理',command=self.start,state='disabled'); self.start_button.pack(side='left')
        self.output_button=ttk.Button(controls,text='保存先を開く',command=self.open_output,state='disabled'); self.output_button.pack(side='left',padx=8)
        self.change_output_button=ttk.Button(controls,text='出力先を変更',command=self.choose_output); self.change_output_button.pack(side='left')
        self.settings_button=ttk.Button(controls,text='設定',command=self.open_settings); self.settings_button.pack(side='right')
        ttk.Button(controls,text='ライセンス',command=lambda:show_licenses(self.root)).pack(side='right',padx=8)
        self.strip_metadata=tk.BooleanVar(value=self.settings['strip_metadata'])
        self.metadata_check=ttk.Checkbutton(main,text='PNGメタデータを削除',variable=self.strip_metadata,command=self.set_strip_metadata)
        self.metadata_check.pack(anchor='w')
        self.status=tk.StringVar(value='未処理 — 画像を追加してから処理を開始してください。')
        ttk.Label(main,textvariable=self.status,font=('Meiryo',11,'bold'),wraplength=1020).pack(anchor='w',pady=5)
        self.progress=ttk.Progressbar(main,mode='determinate'); self.progress.pack(fill='x')
        self.destination=tk.StringVar(value='保存先：入力元直下の mosaicdesk_output')
        ttk.Label(main,textvariable=self.destination,wraplength=1020).pack(anchor='w',pady=5)
        self.settings_text=tk.StringVar(); self.refresh_settings_text()
        ttk.Label(main,textvariable=self.settings_text,wraplength=1020).pack(anchor='w',pady=(0,5))
        ttk.Label(main,text='クリックで拡大・領域編集できます。編集後は「この画像を保存・更新」で同名の出力だけを更新します。',wraplength=1020).pack(anchor='w',pady=(0,10))
        container=ttk.Frame(main); container.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(container,highlightthickness=0)
        bar=ttk.Scrollbar(container,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set); bar.pack(side='right',fill='y'); self.canvas.pack(side='left',fill='both',expand=True)
        self.grid=ttk.Frame(self.canvas); self.grid_id=self.canvas.create_window((0,0),window=self.grid,anchor='nw')
        self.grid.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.grid_id,width=e.width))
        self.canvas.bind('<Enter>',lambda e:root.bind_all('<MouseWheel>',self.wheel))
        self.canvas.bind('<Leave>',lambda e:root.unbind_all('<MouseWheel>'))
        root.protocol('WM_DELETE_WINDOW',self.close); root.after(75,self.poll)

    def wheel(self,event):
        if self.viewer is None: self.canvas.yview_scroll(-int(event.delta/120),'units')

    def choose(self):
        if self.busy: return
        value=filedialog.askdirectory(parent=self.root,title='PNGフォルダを追加')
        if value: self.select_folder(value)

    def choose_images(self):
        if self.busy: return
        values=filedialog.askopenfilenames(parent=self.root,title='PNG画像を追加（複数選択可）',filetypes=[('PNG画像','*.png')])
        if values: self.add_paths(values)

    def on_drop(self,event):
        if not self.busy: self.add_paths(self.root.tk.splitlist(event.data))

    def select_folder(self,value):
        return self.add_paths([value])

    def add_paths(self,values):
        if self.busy: return
        try: images=batch.collect_inputs(values)
        except (OSError,ValueError) as exc:
            messagebox.showerror('画像の追加',str(exc),parent=self.root); return
        seen={batch.path_key(d['source']) for d in self.documents}
        added=0
        for src in images:
            if batch.path_key(src) in seen: continue
            try: regions=self.edit_store.restore(src)
            except OSError: regions=None
            self.documents.append(dict(file=src.name,source=str(src),status='未処理',detections=regions))
            seen.add(batch.path_key(src)); added+=1
        self.folder=images[0].parent
        self.update_output()
        self.progress['value']=0
        self.status.set(f'未処理：一覧 {len(self.documents)}枚 ／ 今回 {added}枚追加 — 一括処理または画像ごとの処理を選べます。')
        if self.output is None: self.status.set('別フォルダの画像が混在しています。「出力先を変更」で共通の保存先を選んでください。')
        self.path_text.set(f'PNG {len(self.documents)}枚 ／ '+('入力元：'+str(self.folder) if len({Path(d['source']).parent for d in self.documents})==1 else '複数の入力フォルダ'))
        self.show_results(); self.update_controls()

    def update_output(self):
        if not self.documents: return
        images=[Path(d['source']) for d in self.documents]
        if not self.output_explicit:
            try: self.output=batch.resolve_output(images)
            except ValueError: self.output=None
        self.report=dict(input=str(images[0].parent),output=str(self.output) if self.output else '',images=self.documents)
        self.destination.set('保存先：'+str(self.output) if self.output else '保存先：共通の出力先を選択してください。')
        if self.output:
            for document in self.documents: document['output_path']=str(self.output/document['file'])

    def choose_output(self):
        if self.busy: return
        value=filedialog.askdirectory(parent=self.root,title='共通の出力先フォルダを選択',initialdir=str(self.output.parent) if self.output else None)
        if value: self.set_output(value)

    def set_output(self,value):
        if self.busy: return
        try: batch.output_paths([Path(d['source']) for d in self.documents],Path(value).resolve())
        except ValueError as exc:
            messagebox.showerror('出力先',str(exc),parent=self.root); return
        if self.image_viewer is not None and not self.image_viewer.close(): return
        self.output=Path(value).resolve(); self.output_explicit=True
        # The edit objects remain intact; pending output destinations must be saved explicitly.
        for document in self.documents: document['status']='未処理'
        self.update_output(); self.show_results(); self.update_controls()
        self.status.set('出力先を変更しました。一括処理または画像ごとの保存で反映します。')

    def clear_inputs(self):
        if self.busy: return
        if self.image_viewer is not None and not self.image_viewer.close(): return
        self.documents.clear(); self.report=None; self.folder=None
        self.output=None; self.output_explicit=False
        self.clear_grid(); self.update_controls()
        self.status.set('未処理 — 画像を追加してください。'); self.path_text.set('PNG画像またはフォルダを追加してください。')
        self.destination.set('保存先：入力元直下の mosaicdesk_output')

    def clear_grid(self):
        for child in self.grid.winfo_children(): child.destroy()
        self.photos.clear()

    def update_controls(self):
        for button in (self.choose_button,self.add_button,self.clear_button,self.change_output_button,self.settings_button,self.metadata_check):
            button.configure(state='disabled' if self.busy else 'normal')
        pending=any(d['status']!='保存済み' for d in self.documents)
        self.start_button.configure(state='normal' if not self.busy and self.output and pending else 'disabled')
        self.output_button.configure(state='normal' if not self.busy and self.output and self.output.is_dir() else 'disabled')

    def parameters(self):
        params=vars(self.args).copy(); params.update(self.settings)
        params.update(output=str(self.output),no_open=True,strip_metadata=self.strip_metadata.get())
        return SimpleNamespace(**params)

    def validate_destination(self):
        if not self.output:
            messagebox.showinfo('出力先の選択','「出力先を変更」で共通の保存先を選んでください。',parent=self.root); return False
        try: batch.output_paths([Path(d['source']) for d in self.documents],self.output)
        except ValueError as exc:
            messagebox.showerror('出力先',str(exc),parent=self.root); return False
        return True

    def start(self,indices=None):
        if self.busy or not self.documents or not self.validate_destination(): return
        if self.image_viewer is not None and not self.image_viewer.close(): return
        selected=indices if indices is not None else [i for i,d in enumerate(self.documents) if d['status']!='保存済み']
        if not selected: return
        params=self.parameters(); params.documents=deepcopy([self.documents[i] for i in selected])
        self.busy=True; self.update_controls(); self.status.set('処理中：検出・保存しています…')
        def work():
            try:
                code=batch.run(params,lambda *event:self.events.put(('progress',event)))
                self.events.put(('finished',code))
            except Exception as exc: self.events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def save_document(self,index,regions):
        if self.busy or not self.validate_destination(): return
        document=self.documents[index]; params=self.parameters()
        self.busy=True; self.update_controls(); self.status.set('保存中：'+document['file']+'を元画像から再生成しています…')
        if self.image_viewer is not None:
            self.image_viewer.save_button.configure(state='disabled')
            self.image_viewer.edit_note.set('元画像から再生成・保存しています…')
        def work():
            try:
                entry,_=batch.process(Path(document['source']),self.output/document['file'],params,regions=regions)
                self.events.put(('saved',(index,entry)))
            except Exception as exc: self.events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def remember(self,entry):
        if entry['status']=='エラー': return
        try: self.edit_store.save(entry)
        except OSError as exc: self.status.set(self.status.get()+' ／ 編集状態の保存に失敗：'+str(exc))

    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='progress':
                    done,total,name,*report=data
                    self.progress.configure(maximum=total,value=done)
                    if report: self.pending_report=report[0]
                    else: self.status.set(f'処理中：{done}/{total}枚完了 — {name}')
                elif kind=='finished':
                    self.busy=False
                    processed=self.pending_report
                    by_source={batch.path_key(d['source']):d for d in self.documents}
                    for entry in processed['images']:
                        by_source[batch.path_key(entry['source'])].update(entry)
                    errors=sum(e['status']=='エラー' for e in processed['images'])
                    self.status.set(f'処理・保存完了：{len(processed["images"])-errors}枚'+(f' ／ エラー {errors}枚' if errors else ''))
                    for entry in processed['images']: self.remember(entry)
                    self.report.update({k:v for k,v in processed.items() if k!='images'})
                    if processed.get('log_file'): self.destination.set('保存先：'+str(self.output)+' ／ ログ：'+processed['log_file'])
                    if processed.get('log_error'): self.status.set(self.status.get()+' ／ ログ保存に失敗：'+processed['log_error'])
                    self.update_controls(); self.show_results()
                elif kind=='saved':
                    index,entry=data
                    self.documents[index].update(entry); self.busy=False
                    self.status.set('保存・更新完了：'+entry['file']+'（他の画像は変更していません）')
                    self.remember(entry)
                    if self.settings['save_log']:
                        try: batch.write_log(dict(output=str(self.output),settings=self.settings,images=[entry]))
                        except OSError as exc: self.status.set(self.status.get()+' ／ ログ保存に失敗：'+str(exc))
                    self.update_controls(); self.show_results()
                    if self.image_viewer is not None and self.image_viewer.index==index: self.image_viewer.mark_saved(entry)
                elif kind=='error':
                    self.busy=False; self.status.set('処理失敗：'+data); self.update_controls()
                    if self.image_viewer is not None:
                        self.image_viewer.close_after_save=False
                        self.image_viewer.save_button.configure(state='normal')
                        self.image_viewer.edit_note.set('保存に失敗しました。編集状態は保持しています。')
                    messagebox.showerror('処理失敗',data,parent=self.root)
        except queue.Empty: pass
        self.root.after(75,self.poll)

    def show_results(self):
        self.clear_grid()
        if self.report is None: return
        for i,entry in enumerate(self.documents):
            card=ttk.Frame(self.grid,padding=10,relief='ridge'); card.grid(row=i//3,column=i%3,sticky='nsew',padx=5,pady=5)
            self.grid.columnconfigure(i%3,weight=1)
            ttk.Label(card,text=entry['file'],wraplength=270).pack(anchor='w')
            if entry['status']=='エラー':
                ttk.Label(card,text='エラー：'+entry.get('error',''),wraplength=270).pack()
            path=Path(entry['output_path']) if entry.get('output_path') and entry['status']=='保存済み' else Path(entry['source'])
            try:
                with Image.open(path) as source:
                    image=source.convert('RGB'); image.thumbnail((270,185)); scale=image.width/source.width
                    draw=ImageDraw.Draw(image)
                    for region in entry.get('detections') or []: draw.rectangle([round(v*scale) for v in region['box']],outline='#ff4050',width=2)
                    photo=ImageTk.PhotoImage(image)
                self.photos.append(photo)
                ttk.Button(card,image=photo,command=lambda idx=i:self.open_viewer(idx)).pack(pady=5)
            except (OSError,ValueError):
                ttk.Label(card,text='プレビューできません',wraplength=270).pack()
            count=len(entry.get('detections') or [])
            ttk.Label(card,text=f'{entry["status"]} ／ 領域 {count}',wraplength=270).pack(anchor='w')
            actions=ttk.Frame(card); actions.pack(anchor='w',pady=5)
            ttk.Button(actions,text='拡大・領域を編集',command=lambda idx=i:self.open_viewer(idx)).pack(side='left')
            ttk.Button(actions,text='この画像だけ処理',command=lambda idx=i:self.start([idx])).pack(side='left',padx=4)

    def open_viewer(self,index):
        if self.busy: return
        if self.image_viewer is not None and not self.image_viewer.close(): return
        try:
            self.image_viewer=ImageViewer(self,index); self.viewer=self.image_viewer.window
            return self.viewer
        except Exception as exc:
            messagebox.showerror('画像表示エラー',str(exc),parent=self.root)

    def refresh_settings_text(self):
        s=self.settings
        self.settings_text.set(f'設定：ブロック {s["block"]}px ／ 余白 {s["padding"]}px ／ しきい値 {s["confidence"]:g} ／ 処理ログ '+('オン' if s['save_log'] else 'オフ')+(' ／ '+self.settings_warning if self.settings_warning else ''))

    def save_settings(self,values):
        settings=validate_settings(values)
        self.settings_path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
        self.settings=settings; self.strip_metadata.set(settings['strip_metadata'])
        self.settings_warning=''; self.refresh_settings_text()

    def set_strip_metadata(self):
        try: self.save_settings({**self.settings,'strip_metadata':self.strip_metadata.get()})
        except OSError as exc: messagebox.showerror('設定を保存できません',str(exc),parent=self.root)

    def open_output(self):
        if self.output and self.output.is_dir(): os.startfile(str(self.output))

    def close(self):
        if self.busy:
            messagebox.showinfo('処理中','保存が完了してから閉じてください。',parent=self.root); return
        if self.image_viewer is not None and not self.image_viewer.close(): return
        self.root.destroy()

    def open_settings(self):
        if self.busy: return
        if self.settings_window is not None and self.settings_window.winfo_exists(): self.settings_window.lift(); return
        window=tk.Toplevel(self.root); self.settings_window=window
        window.title('モザイク設定'); window.geometry('640x480'); window.resizable(False,False); window.transient(self.root)
        main=ttk.Frame(window,padding=20); main.pack(fill='both',expand=True)
        values={key:tk.StringVar(value=str(self.settings[key])) for key in ('block','padding','confidence')}
        for row,(key,title,help_text) in enumerate((('block','モザイクのブロック幅（px）','大きいほどモザイクが粗くなります。2以上。'),('padding','検出領域の余白（px）','検出矩形の外側に追加する範囲です。0以上。'),('confidence','検出しきい値','0〜1。低くすると候補が増えますが、誤検出も増える可能性があります。'))):
            ttk.Label(main,text=title).grid(row=row*2,column=0,sticky='w',pady=(0,3))
            ttk.Entry(main,textvariable=values[key],width=14).grid(row=row*2,column=1,sticky='w',padx=12)
            ttk.Label(main,text=help_text,wraplength=575).grid(row=row*2+1,column=0,columnspan=2,sticky='w',pady=(0,12))
        nipple=tk.BooleanVar(value=self.settings['include_nipple']); log=tk.BooleanVar(value=self.settings['save_log'])
        ttk.Checkbutton(main,text='乳首も検出対象に含める',variable=nipple).grid(row=6,column=0,columnspan=2,sticky='w')
        ttk.Checkbutton(main,text='モザイク処理ログを残す（検出領域・設定・エラー）',variable=log).grid(row=7,column=0,columnspan=2,sticky='w',pady=6)
        ttk.Label(main,text='ログは完成画像フォルダの外に保存します。オフならPNG以外は保存しません。',wraplength=575).grid(row=9,column=0,columnspan=2,sticky='w')
        strip=tk.BooleanVar(value=self.settings['strip_metadata'])
        ttk.Checkbutton(main,text='PNGメタデータを削除',variable=strip).grid(row=8,column=0,columnspan=2,sticky='w',pady=4)
        buttons=ttk.Frame(main); buttons.grid(row=10,column=0,columnspan=2,sticky='e',pady=16)
        def apply():
            try:
                self.save_settings({**{k:v.get() for k,v in values.items()},'include_nipple':nipple.get(),'save_log':log.get(),'strip_metadata':strip.get()})
            except (ValueError,OSError) as exc:
                messagebox.showerror('設定を保存できません',str(exc),parent=window); return
            window.destroy(); self.settings_window=None
        ttk.Button(buttons,text='キャンセル',command=window.destroy).pack(side='left',padx=10)
        ttk.Button(buttons,text='保存して閉じる',command=apply).pack(side='left')
        window.grab_set(); window.focus_set()
        return window




def launch(args):
    root=TkinterDnD.Tk()
    app=App(root,args)
    if args.input: app.add_paths(args.input if isinstance(args.input,list) else [args.input])
    root.mainloop()
    return 0


def show_licenses(parent=None):
    owns_root=parent is None
    if owns_root:
        parent=tk.Tk(); parent.withdraw()
    window=tk.Toplevel(parent); window.title('ライセンス・第三者著作権表示'); window.geometry('900x680')
    from tkinter.scrolledtext import ScrolledText
    text=ScrolledText(window,wrap='word',font=('Consolas',10)); text.pack(fill='both',expand=True,padx=12,pady=12)
    for name in ('LICENSE','THIRD_PARTY_NOTICES.txt'):
        text.insert('end',name+'\n'+'='*60+'\n'+(batch.RESOURCES/name).read_text(encoding='utf-8')+'\n\n')
    text.configure(state='disabled')
    def close():
        window.destroy()
        if owns_root: parent.destroy()
    ttk.Button(window,text='閉じる',command=close).pack(pady=(0,12)); window.protocol('WM_DELETE_WINDOW',close)
    if owns_root: parent.mainloop()
    return 0


def check_ui(args):
    """Packaged UI regression check, kept entirely off-screen with temporary fixtures."""
    import tempfile, time
    with tempfile.TemporaryDirectory(prefix='mosaicdesk-ui-') as temporary:
        parent=Path(temporary)
        original=parent/'original'; modified=parent/'modified'
        original.mkdir(); modified.mkdir()
        Image.new('RGB',(600,800),'blue').save(original/'fixture.png')
        Image.new('RGB',(600,800),'red').save(modified/'fixture.png')
        previous_state_root,previous_root=batch.STATE_ROOT,batch.ROOT
        batch.STATE_ROOT=parent/'state'; batch.ROOT=parent
        root=TkinterDnD.Tk(); root.withdraw()
        try:
            app=App(root,args)
            app.report=dict(input=str(original),output=str(modified),images=[dict(file='fixture.png',size=[600,800],detections=[],status='検出なし・要確認')])
            root.geometry('1060x780+4000+4000'); root.deiconify(); root.update()
            window=app.open_viewer(0)
            if window is None: raise RuntimeError('Viewer failed to open.')
            window.geometry('1200x850+4000+4000')
            deadline=time.monotonic()+4
            while not app.image_viewer.photos and time.monotonic()<deadline:
                root.update(); time.sleep(.02)
            viewer=app.image_viewer
            assert window.winfo_ismapped() and viewer.display_sizes[0][0]>270
            for mode,colors in [('modified',[(255,0,0)]),('original',[(0,0,255)]),('both',[(0,0,255),(255,0,0)])]:
                viewer.mode.set(mode); viewer.render()
                assert len(viewer.photos)==len(colors)
                for photo,color in zip(viewer.photos,colors):
                    actual=viewer.canvas.tk.call(str(photo),'get',0,0)
                    assert tuple(actual)==color,(actual,color)
            viewer.zoom.set('200%'); viewer.render()
            assert viewer.display_sizes==[(1200,1600),(1200,1600)]
            window.winfo_children()[0].winfo_children()[0].invoke()
            assert app.viewer is None
            settings=app.open_settings()
            settings.update_idletasks(); assert settings.winfo_exists(); settings.destroy()
            # Exercise real packaged imports, detector, worker queue, edit controls and atomic save.
            from PIL import PngImagePlugin
            import numpy as np
            meta=PngImagePlugin.PngInfo(); meta.add_text('parameters','synthetic fixture'); meta.add_itxt('workflow','synthetic workflow')
            exif=Image.Exif(); exif[274]=6
            Image.fromarray(np.random.default_rng(44).integers(0,256,(240,320,3),dtype=np.uint8)).save(original/'edit.png',pnginfo=meta,exif=exif)
            app.clear_inputs(); app.add_paths([original/'fixture.png',original/'edit.png'])
            app.start()
            def wait_save():
                limit=time.monotonic()+30
                while app.busy and time.monotonic()<limit:
                    root.update(); time.sleep(.02)
                assert not app.busy and '失敗' not in app.status.get(),app.status.get()
            wait_save()
            assert all(d['status']=='保存済み' for d in app.documents)
            output=original/'mosaicdesk_output'
            assert app.output==output and len(list(output.iterdir()))==2
            untouched=(output/'fixture.png').read_bytes()
            regions=[dict(id='a',box=[10,10,40,40],block=8),dict(id='b',box=[80,30,115,65],block=8),dict(id='c',box=[160,100,200,150],block=8)]
            app.save_document(1,regions); wait_save()
            app.open_viewer(1); editor=app.image_viewer
            editor.window.geometry('1000x680+4000+4000'); root.update(); editor.render()
            assert editor.save_button.winfo_ismapped()
            assert editor.save_button.winfo_y()+editor.save_button.winfo_height()<=editor.panel.winfo_height()
            editor.selected_id='b'; editor.refresh_fields()
            editor.fields['x'].set('85'); editor.fields['y'].set('70'); editor.fields['block'].set('12')
            editor.apply_fields(); edited=deepcopy(editor.regions)
            assert edited[0]==regions[0] and edited[2]==regions[2]
            editor.undo(); assert editor.regions==regions
            editor.redo(); assert editor.regions==edited
            editor.reset_regions(); assert editor.regions==[]
            editor.undo(); assert editor.regions==edited
            editor.save_button.invoke(); wait_save()
            assert not editor.unsaved and (output/'fixture.png').read_bytes()==untouched
            assert not set(batch.png_chunk_types(output/'edit.png'))&{b'tEXt',b'zTXt',b'iTXt',b'eXIf'}
            assert sorted(p.name for p in output.iterdir())==['edit.png','fixture.png']
            editor.close()
            app.clear_inputs(); app.add_paths([original/'edit.png'])
            assert app.documents[0]['detections']==edited
        finally:
            root.destroy()
            batch.STATE_ROOT,batch.ROOT=previous_state_root,previous_root
    return 0
