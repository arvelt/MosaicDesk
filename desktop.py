"""Desktop entry point and local review viewer."""
import os, queue, threading, json, math
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import TkinterDnD, DND_FILES
from PIL import Image, ImageTk, ImageDraw
import batch


def validate_settings(values):
    block=int(values['block']); padding=int(values['padding']); confidence=float(values['confidence'])
    if block<2: raise ValueError('モザイクのブロック幅は2px以上にしてください。')
    if padding<0: raise ValueError('余白は0px以上にしてください。')
    if not math.isfinite(confidence) or not 0<=confidence<=1: raise ValueError('検出しきい値は0〜1にしてください。')
    return dict(block=block,padding=padding,confidence=confidence,include_nipple=bool(values['include_nipple']),save_log=bool(values['save_log']))


class ImageViewer:
    def __init__(self, app, index):
        self.app=app
        self.report=app.report
        self.index=index
        entry=self.report['images'][index]
        self.window=tk.Toplevel(app.root)
        self.window.title('画像確認 — '+entry['file'])
        self.window.geometry('1200x850')
        self.window.minsize(800,550)
        self.window.transient(app.root)
        self.mode=tk.StringVar(value='modified')
        self.zoom=tk.StringVar(value='画面に合わせる')
        self.pending=None
        self.images={}
        self.photos=[]
        self.display_sizes=[]
        # Fully load images before closing their files; rendering never depends on a closed stream.
        for key,folder in (('original',self.report['input']),('modified',self.report['output'])):
            with Image.open(Path(folder)/entry['file']) as image:
                self.images[key]=image.convert('RGBA')
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
        self.canvas=tk.Canvas(body,bg='#20242b',highlightthickness=0)
        ys=ttk.Scrollbar(body,orient='vertical',command=self.canvas.yview); ys.pack(side='right',fill='y')
        xs=ttk.Scrollbar(body,orient='horizontal',command=self.canvas.xview); xs.pack(side='bottom',fill='x')
        self.canvas.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',self.schedule_render)
        self.canvas.bind('<Map>',self.schedule_render)
        self.canvas.bind('<MouseWheel>',lambda e:self.canvas.yview_scroll(-int(e.delta/120),'units'))
        self.canvas.bind('<Shift-MouseWheel>',lambda e:self.canvas.xview_scroll(-int(e.delta/120),'units'))
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
        for i,key in enumerate(keys):
            shown=self.images[key].resize(display,Image.Resampling.LANCZOS)
            photo=ImageTk.PhotoImage(shown,master=self.window)
            self.photos.append(photo); self.display_sizes.append(display)
            x=offset+gap+i*(display[0]+gap)
            self.canvas.create_text(x,8,text='原画' if key=='original' else '修正画像（保存済み）',anchor='nw',fill='white',font=('Meiryo',11,'bold'))
            self.canvas.create_image(x,top,image=photo,anchor='nw')
        self.canvas.configure(scrollregion=(0,0,max(width,content_width),max(height,display[1]+top+16)))
        self.canvas.xview_moveto(0); self.canvas.yview_moveto(0)
        self.zoom_note.set(f'{factor*100:.0f}% ／ 原寸 {source.width} × {source.height}px')

    def close(self):
        if self.pending is not None: self.window.after_cancel(self.pending)
        self.window.destroy(); self.app.viewer=None; self.app.image_viewer=None


class App:
    def __init__(self, root, args):
        self.root, self.args = root, args
        self.folder = None
        self.report = None
        self.busy = False
        self.events = queue.Queue()
        self.photos = []
        self.viewer = None
        self.image_viewer = None
        self.settings_window = None
        self.settings_path=batch.ROOT/'settings.json'
        self.settings=validate_settings(dict(block=args.block,padding=args.padding,confidence=args.confidence,include_nipple=args.include_nipple,save_log=getattr(args,'save_log',False)))
        self.settings_warning=''
        if self.settings_path.exists():
            try:
                self.settings=validate_settings(json.loads(self.settings_path.read_text(encoding='utf-8')))
            except (OSError,ValueError,KeyError,TypeError) as exc:
                self.settings_warning='設定ファイルを読めなかったため、初期設定を使用しています。'
        root.title('MosaicDesk')
        root.geometry('1060x780')
        root.minsize(820,600)
        root.option_add('*Font', 'Meiryo 10')
        main=ttk.Frame(root,padding=18); main.pack(fill='both',expand=True)
        ttk.Label(main,text='MosaicDesk',font=('Meiryo',20,'bold')).pack(anchor='w')
        self.drop=ttk.Frame(main,padding=18,relief='ridge')
        self.drop.pack(fill='x',pady=(12,8))
        drop_label=ttk.Label(self.drop,text='ここにPNGフォルダをドラッグ＆ドロップ',anchor='center')
        drop_label.pack(fill='x',pady=(0,10))
        self.choose_button=ttk.Button(self.drop,text='フォルダを選ぶ',command=self.choose)
        self.choose_button.pack()
        for target in (root, self.drop, drop_label, self.choose_button):
            target.drop_target_register(DND_FILES)
            target.dnd_bind('<<Drop>>',self.on_drop)
        self.path_text=tk.StringVar(value='フォルダ未選択')
        ttk.Label(main,textvariable=self.path_text,wraplength=950).pack(anchor='w')
        controls=ttk.Frame(main); controls.pack(fill='x',pady=10)
        self.start_button=ttk.Button(controls,text='モザイク処理を開始',command=self.start,state='disabled'); self.start_button.pack(side='left',padx=10)
        self.output_button=ttk.Button(controls,text='保存先を開く',command=self.open_output,state='disabled'); self.output_button.pack(side='left')
        self.settings_button=ttk.Button(controls,text='設定',command=self.open_settings); self.settings_button.pack(side='right')
        ttk.Button(controls,text='ライセンス',command=lambda:show_licenses(self.root)).pack(side='right',padx=8)
        self.status=tk.StringVar(value='未処理 — フォルダを入れてから「モザイク処理を開始」を押してください。')
        ttk.Label(main,textvariable=self.status,font=('Meiryo',11,'bold'),wraplength=980).pack(anchor='w',pady=(0,5))
        self.progress=ttk.Progressbar(main,mode='determinate'); self.progress.pack(fill='x')
        self.destination=tk.StringVar(value='保存先：元フォルダの隣に「元フォルダ名-censored」を作成します。')
        ttk.Label(main,textvariable=self.destination,wraplength=980).pack(anchor='w',pady=5)
        self.settings_text=tk.StringVar(); self.refresh_settings_text()
        ttk.Label(main,textvariable=self.settings_text,wraplength=980).pack(anchor='w',pady=(0,5))
        ttk.Label(main,text='処理・保存が終わると、下に完成画像が表示されます。クリックで拡大できます。',wraplength=980).pack(anchor='w',pady=(0,10))
        container=ttk.Frame(main); container.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(container,highlightthickness=0)
        bar=ttk.Scrollbar(container,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y'); self.canvas.pack(side='left',fill='both',expand=True)
        self.grid=ttk.Frame(self.canvas)
        self.grid_id=self.canvas.create_window((0,0),window=self.grid,anchor='nw')
        self.grid.bind('<Configure>',lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.grid_id,width=e.width))
        self.canvas.bind('<Enter>',lambda e:root.bind_all('<MouseWheel>',self.wheel))
        self.canvas.bind('<Leave>',lambda e:root.unbind_all('<MouseWheel>'))
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.after(75,self.poll)

    def wheel(self,event):
        if self.viewer is None: self.canvas.yview_scroll(-int(event.delta/120),'units')

    def choose(self):
        value=filedialog.askdirectory(parent=self.root,title='処理するPNGフォルダを選択')
        if value: self.select_folder(value)

    def on_drop(self,event):
        paths=self.root.tk.splitlist(event.data)
        if len(paths)!=1:
            messagebox.showinfo('フォルダの指定','フォルダを1つずつ入れてください。',parent=self.root)
            return
        self.select_folder(paths[0])

    def select_folder(self,value):
        if self.busy: return
        if self.image_viewer is not None: self.image_viewer.close()
        folder=Path(value).resolve()
        if not folder.is_dir():
            messagebox.showerror('フォルダの指定','PNGが入ったフォルダをドロップしてください。',parent=self.root); return
        count=sum(p.is_file() and p.suffix.lower()=='.png' for p in folder.iterdir())
        if not count:
            messagebox.showerror('PNGがありません','フォルダ直下にPNGがありません。',parent=self.root); return
        self.folder=folder; self.report=None
        self.clear_grid()
        self.path_text.set(str(folder))
        self.status.set(f'未処理：PNG {count}枚 — 「モザイク処理を開始」を押してください。')
        self.destination.set('保存予定：'+str(batch.output_folder(folder)))
        self.progress['value']=0
        self.start_button.configure(state='normal'); self.output_button.configure(state='disabled')

    def clear_grid(self):
        for child in self.grid.winfo_children(): child.destroy()
        self.photos.clear()

    def start(self):
        if self.busy or self.folder is None: return
        if self.image_viewer is not None: self.image_viewer.close()
        self.busy=True; self.clear_grid()
        self.settings_button.configure(state='disabled')
        self.start_button.configure(state='disabled'); self.choose_button.configure(state='disabled'); self.output_button.configure(state='disabled')
        self.status.set('処理中：検出モデルを読み込んでいます…')
        params=vars(self.args).copy(); params.update(self.settings); params.update(input=str(self.folder),output=None,no_open=True)
        def work():
            try:
                code=batch.run(SimpleNamespace(**params),lambda *event:self.events.put(('progress',event)))
                self.events.put(('finished',code))
            except Exception as exc:
                self.events.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=='progress':
                    done,total,name,*report=data
                    self.progress.configure(maximum=total,value=done)
                    if report:
                        self.report=report[0]
                        self.destination.set('保存済み：'+self.report['output'])
                    else:
                        self.status.set(f'処理中：{done}/{total}枚完了 — {name}')
                elif kind=='finished':
                    self.busy=False
                    errors=sum(e['status']=='エラー' for e in self.report['images'])
                    total=len(self.report['images'])
                    self.status.set(f'処理・保存完了：{total-errors}枚'+(f' ／ エラー{errors}枚' if errors else '')+' — 下の完成画像を拡大して確認してください。')
                    self.choose_button.configure(state='normal'); self.start_button.configure(state='normal'); self.output_button.configure(state='normal')
                    self.settings_button.configure(state='normal')
                    if self.report.get('log_file'): self.destination.set('保存済み：'+self.report['output']+' ／ ログ：'+self.report['log_file'])
                    if self.report.get('log_error'): self.status.set(self.status.get()+' ／ ログ保存に失敗しました：'+self.report['log_error'])
                    self.show_results()
                elif kind=='error':
                    self.busy=False
                    self.status.set('処理失敗：'+data)
                    self.choose_button.configure(state='normal'); self.start_button.configure(state='normal')
                    self.settings_button.configure(state='normal')
                    messagebox.showerror('処理失敗',data,parent=self.root)
        except queue.Empty: pass
        self.root.after(75,self.poll)

    def show_results(self):
        self.clear_grid()
        out=Path(self.report['output'])
        for i,entry in enumerate(self.report['images']):
            card=ttk.Frame(self.grid,padding=10,relief='ridge'); card.grid(row=i//3,column=i%3,sticky='nsew',padx=5,pady=5)
            self.grid.columnconfigure(i%3,weight=1)
            ttk.Label(card,text=entry['file'],wraplength=270).pack(anchor='w')
            if entry['status']=='エラー':
                ttk.Label(card,text='エラー：'+entry['error'],wraplength=270).pack(); continue
            with Image.open(out/entry['file']) as source:
                image=source.convert('RGB')
                image.thumbnail((270,215))
                scale=image.width/source.width
                draw=ImageDraw.Draw(image)
                for detection in entry['detections']:
                    draw.rectangle([round(v*scale) for v in detection['box']],outline='#ff4050',width=2)
                photo=ImageTk.PhotoImage(image)
            self.photos.append(photo)
            ttk.Button(card,image=photo,command=lambda idx=i:self.open_viewer(idx)).pack(pady=5)
            label=f'検出 {len(entry["detections"])}領域' if entry['detections'] else '検出なし（原画をコピー）'
            ttk.Label(card,text=label+' ／ 保存済み',wraplength=270).pack(anchor='w')
            ttk.Button(card,text='完成画像を拡大',command=lambda idx=i:self.open_viewer(idx)).pack(anchor='w',pady=5)

    def open_viewer(self,index):
        if self.image_viewer is not None: self.image_viewer.close()
        try:
            self.image_viewer=ImageViewer(self,index)
            self.viewer=self.image_viewer.window
            return self.viewer
        except Exception as exc:
            if self.image_viewer is not None: self.image_viewer.close()
            messagebox.showerror('画像表示エラー',str(exc),parent=self.root)

    def refresh_settings_text(self):
        s=self.settings
        self.settings_text.set(f'設定：ブロック {s["block"]}px ／ 余白 {s["padding"]}px ／ しきい値 {s["confidence"]:g} ／ 処理ログ '+('オン' if s['save_log'] else 'オフ')+(' ／ '+self.settings_warning if self.settings_warning else ''))

    def save_settings(self,values):
        settings=validate_settings(values)
        self.settings_path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
        self.settings=settings; self.settings_warning=''; self.refresh_settings_text()

    def open_settings(self):
        if self.busy: return
        if self.settings_window is not None and self.settings_window.winfo_exists(): self.settings_window.lift(); return
        window=tk.Toplevel(self.root); self.settings_window=window
        window.title('モザイク設定'); window.geometry('640x430'); window.resizable(False,False); window.transient(self.root)
        main=ttk.Frame(window,padding=20); main.pack(fill='both',expand=True)
        values={key:tk.StringVar(value=str(self.settings[key])) for key in ('block','padding','confidence')}
        for row,(key,title,help_text) in enumerate((('block','モザイクのブロック幅（px）','大きいほどモザイクが粗くなります。2以上。'),('padding','検出領域の余白（px）','検出矩形の外側に追加する範囲です。0以上。'),('confidence','検出しきい値','0〜1。低くすると候補が増えますが、誤検出も増える可能性があります。'))):
            ttk.Label(main,text=title).grid(row=row*2,column=0,sticky='w',pady=(0,3))
            ttk.Entry(main,textvariable=values[key],width=14).grid(row=row*2,column=1,sticky='w',padx=12)
            ttk.Label(main,text=help_text,wraplength=575).grid(row=row*2+1,column=0,columnspan=2,sticky='w',pady=(0,12))
        nipple=tk.BooleanVar(value=self.settings['include_nipple']); log=tk.BooleanVar(value=self.settings['save_log'])
        ttk.Checkbutton(main,text='乳首も検出対象に含める',variable=nipple).grid(row=6,column=0,columnspan=2,sticky='w')
        ttk.Checkbutton(main,text='モザイク処理ログを残す（検出領域・設定・エラー）',variable=log).grid(row=7,column=0,columnspan=2,sticky='w',pady=6)
        ttk.Label(main,text='ログは完成画像フォルダの外に保存します。オフならPNG以外は保存しません。',wraplength=575).grid(row=8,column=0,columnspan=2,sticky='w')
        buttons=ttk.Frame(main); buttons.grid(row=9,column=0,columnspan=2,sticky='e',pady=16)
        def apply():
            try:
                self.save_settings({**{k:v.get() for k,v in values.items()},'include_nipple':nipple.get(),'save_log':log.get()})
            except (ValueError,OSError) as exc:
                messagebox.showerror('設定を保存できません',str(exc),parent=window); return
            window.destroy(); self.settings_window=None
        ttk.Button(buttons,text='キャンセル',command=window.destroy).pack(side='left',padx=10)
        ttk.Button(buttons,text='保存して閉じる',command=apply).pack(side='left')
        window.grab_set(); window.focus_set()
        return window

    def open_output(self):
        if self.report: os.startfile(self.report['output'])

    def close(self):
        if self.busy:
            messagebox.showinfo('処理中','保存が完了してから閉じてください。',parent=self.root); return
        self.root.destroy()


def launch(args):
    root=TkinterDnD.Tk()
    app=App(root,args)
    if args.input: app.select_folder(args.input)
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
        finally:
            root.destroy()
    return 0
