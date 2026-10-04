"""Local PNG rendering, always from the original and the current region objects."""
import os, sys
from pathlib import Path
ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, '_MEIPASS', ROOT))
STATE_ROOT = Path(os.environ.get('LOCALAPPDATA', ROOT)) / 'MosaicDesk'
os.environ['HF_HOME'] = str(RESOURCES / 'cache' / 'huggingface')
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['ONNX_MODE'] = 'cpu'
import argparse, hashlib, json, math, shutil, tempfile, time, traceback, uuid
import numpy as np
from PIL import Image, PngImagePlugin
from imgutils.detect import detect_censors

SUPPORTED_EXTENSIONS = {'.png'}


def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def collect_inputs(values):
    if isinstance(values, (str, Path)):
        values = [values]
    images, seen = [], set()
    for value in values:
        path = Path(value).resolve()
        if path.is_dir():
            candidates = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
        elif path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            candidates = [path]
        else:
            raise ValueError('PNGファイルまたはPNGが入ったフォルダを指定してください。')
        for src in candidates:
            key = path_key(src)
            if key not in seen:
                seen.add(key); images.append(src)
    if not images:
        raise ValueError('フォルダ直下にPNGがありません。')
    return images


def output_folder(folder):
    return Path(folder) / 'mosaicdesk_output'


def resolve_output(images, explicit=None):
    parents = {p.parent for p in images}
    if explicit:
        return Path(explicit).resolve()
    if len(parents) != 1:
        raise ValueError('別フォルダの画像が混在しています。「出力先を変更」で共通の保存先を選んでください。')
    return output_folder(next(iter(parents)))


def output_paths(images, folder):
    targets, seen = [], set()
    for src in images:
        target = Path(folder) / src.name
        key = path_key(target)
        if key == path_key(src):
            raise ValueError('元画像と同じファイルへ保存できません。別の出力フォルダを選んでください。')
        if key in seen:
            raise ValueError(f'同じファイル名「{src.name}」が別フォルダにあります。一覧を分けて処理してください。')
        seen.add(key); targets.append(target)
    # A target must never replace ANY of the originals, including another listed input.
    originals = {path_key(p) for p in images}
    if any(path_key(p) in originals for p in targets):
        raise ValueError('保存先に入力元画像が含まれています。別の保存先を選んでください。')
    return targets


def expand_region(box, margin, width, height):
    limits = np.array([width, height, width, height], dtype=np.int64)
    corners = np.asarray(box, dtype=np.int64) + np.array([-margin, -margin, margin, margin], dtype=np.int64)
    region = np.clip(corners, 0, limits)
    if np.any(region[2:] <= region[:2]):
        raise ValueError('検出領域が画像内の有効な矩形になりません。')
    return tuple(int(coordinate) for coordinate in region)


def load_original(src):
    with open(src, 'rb') as stream:
        header = stream.read(26)
    if len(header) < 26 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[24] > 8:
        raise ValueError('Only 8-bit or lower PNG is supported; 16-bit PNG is rejected without modification.')
    with Image.open(src) as im:
        if im.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P') or getattr(im, 'n_frames', 1) != 1:
            raise ValueError('単一フレームの8bit以下のPNGに対応しています。元画像は変更しません。')
        meta = im.info.copy()
        image = im.convert('RGBA') if im.mode == 'P' or 'transparency' in meta else im.copy()
    return image, meta


def normalize_regions(regions, size, default_block):
    result, ids = [], set()
    for region in regions:
        item = dict(region)
        item['box'] = list(expand_region(item['box'], 0, *size))
        item['block'] = int(item.get('block', default_block))
        if item['block'] < 2:
            raise ValueError('モザイクのブロック幅は2px以上にしてください。')
        item['id'] = str(item.get('id') or uuid.uuid4().hex)
        if item['id'] in ids:
            raise ValueError('領域IDが重複しています。')
        ids.add(item['id']); result.append(item)
    return result


def pixelate(image, boxes, block):
    result = image.copy()
    for region in boxes:
        box = region['box'] if isinstance(region, dict) else region
        strength = region.get('block', block) if isinstance(region, dict) else block
        if int(strength) < 2:
            raise ValueError('モザイクのブロック幅は2px以上にしてください。')
        # Every region is sampled from the original, never from the previous result.
        crop = image.crop(box)
        tiny = crop.resize((max(1, math.ceil(crop.width/strength)), max(1, math.ceil(crop.height/strength))), Image.Resampling.BOX)
        modified = tiny.resize(crop.size, Image.Resampling.NEAREST)
        if image.mode in ('RGBA', 'LA'):
            modified.putalpha(crop.getchannel('A'))
        result.paste(modified, box)
    return result


def png_chunk_types(path):
    chunks = []
    with open(path, 'rb') as stream:
        if stream.read(8) != b'\x89PNG\r\n\x1a\n':
            raise ValueError('Invalid PNG.')
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError('Incomplete PNG.')
            length, kind = int.from_bytes(header[:4], 'big'), header[4:]
            chunks.append(kind)
            stream.seek(length + 4, 1)
            if kind == b'IEND':
                return chunks


def process(src, out, args, detector=None, regions=None):
    src, out = Path(src).resolve(), Path(out).resolve()
    if src == out:
        raise ValueError('元画像を上書きできません。')
    image, meta = load_original(src)
    if regions is None:
        labels = {'pussy', 'penis'} | ({'nipple_f'} if args.include_nipple else set())
        raw = (detector or detect_censors)(image.convert('RGB'), level='s', conf_threshold=args.confidence)
        regions = [dict(box=list(expand_region(b, args.padding, *image.size)), label=l, confidence=float(c), block=args.block) for b, l, c in raw if l in labels]
    regions = normalize_regions(regions, image.size, args.block)
    result = pixelate(image, regions, args.block)
    strip = getattr(args, 'strip_metadata', True)
    mask = np.zeros((image.height, image.width), dtype=bool)
    for region in regions:
        x0, y0, x1, y1 = region['box']; mask[y0:y1, x0:x1] = True
    out.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.mosaicdesk-', suffix='.png', dir=out.parent)
    os.close(descriptor); temporary = Path(temporary)
    try:
        if not regions and not strip:
            shutil.copy2(src, temporary)
        else:
            # Pillow otherwise implicitly carries eXIf from Image.info into the new PNG.
            result.info.clear()
            pnginfo = PngImagePlugin.PngInfo()
            if not strip:
                for key, value in meta.items():
                    if isinstance(value, str): pnginfo.add_itxt(key, value)
            kwargs = {k: meta[k] for k in ('icc_profile', 'dpi') if k in meta}
            if not strip and 'exif' in meta: kwargs['exif'] = meta['exif']
            result.save(temporary, format='PNG', pnginfo=pnginfo, **kwargs)
        with Image.open(temporary) as saved:
            saved = saved.convert('RGBA') if saved.mode == 'P' or 'transparency' in saved.info else saved.copy()
        if saved.size != image.size or saved.mode != image.mode or not np.array_equal(np.asarray(image)[~mask], np.asarray(saved)[~mask]):
            raise RuntimeError('Output verification failed: size or pixels outside mask changed.')
        if image.mode in ('RGBA', 'LA') and not np.array_equal(np.asarray(image.getchannel('A')), np.asarray(saved.getchannel('A'))):
            raise RuntimeError('Alpha verification failed.')
        if strip and set(png_chunk_types(temporary)) & {b'tEXt', b'zTXt', b'iTXt', b'eXIf'}:
            raise RuntimeError('生成メタデータの除去を確認できませんでした。')
        os.replace(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)
    entry = dict(file=src.name, source=str(src), output_path=str(out), source_digest=hashlib.sha256(src.read_bytes()).hexdigest(), size=list(image.size),
                 status='保存済み', detections=regions, outside_pixels_equal=True, metadata_removed=strip)
    return entry, result


def write_log(report):
    folder = Path(report['output'])
    path = folder.with_name(folder.name + '.processing.json')
    # Logs are optional and kept outside the finished-image folder.
    with path.open('w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    report['log_file'] = str(path)


def run(args, progress=None):
    documents = getattr(args, 'documents', None)
    values = getattr(args, 'inputs', None) or args.input
    images = [Path(d['source']).resolve() for d in documents] if documents is not None else collect_inputs(values)
    if not images: raise ValueError('PNGを追加してください。')
    out = resolve_output(images, args.output)
    targets = output_paths(images, out)
    entries, start = [], time.perf_counter()
    for i, (src, target) in enumerate(zip(images, targets), 1):
        if progress: progress(i - 1, len(images), src.name)
        print(f'[{i}/{len(images)}] {src.name}', flush=True)
        try:
            regions = documents[i - 1].get('detections') if documents is not None else None
            entry, _ = process(src, target, args, regions=regions)
        except Exception as exc:
            traceback.print_exc()
            entry = dict(file=src.name, source=str(src), status='エラー', error=str(exc))
        entries.append(entry)
    settings = {k: getattr(args, k, None) for k in ('block', 'padding', 'confidence', 'include_nipple', 'save_log', 'strip_metadata')}
    report = dict(input=str(images[0].parent), output=str(out), seconds=round(time.perf_counter() - start, 2), settings=settings, images=entries)
    if getattr(args, 'save_log', False):
        try: write_log(report)
        except OSError as exc: report['log_error'] = str(exc)
    if progress: progress(len(images), len(images), None, report)
    if not args.no_open: os.startfile(str(out))
    return int(any(e['status'] == 'エラー' for e in entries))


def main():
    parser = argparse.ArgumentParser(description='Local PNG mosaic with individual region editing.')
    parser.add_argument('input', nargs='*')
    parser.add_argument('--output')
    parser.add_argument('--confidence', type=float, default=.25)
    parser.add_argument('--padding', type=int, default=10)
    parser.add_argument('--block', type=int, default=20)
    parser.add_argument('--include-nipple', action='store_true')
    parser.add_argument('--save-log', action='store_true')
    parser.add_argument('--keep-png-metadata', dest='strip_metadata', action='store_false', default=True)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--check-ui', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--licenses', action='store_true')
    args = parser.parse_args()
    if args.licenses:
        from desktop import show_licenses
        return show_licenses()
    if not 0 <= args.confidence <= 1 or args.padding < 0 or args.block < 2: parser.error('Invalid confidence/padding/block.')
    if args.check_ui:
        from desktop import check_ui
        return check_ui(args)
    if not args.input or not args.no_open:
        from desktop import launch
        return launch(args)
    return run(args)

if __name__ == '__main__':
    if sys.stdout is None:
        import io
        sys.stdout = io.StringIO(); sys.stderr = io.StringIO()
    try: sys.exit(main())
    except Exception:
        traceback.print_exc(); sys.exit(1)
