import io
import json
import os
import subprocess
import tempfile
import warnings
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfReader

MAX_FILE = 3*1024*1024
Image.MAX_IMAGE_PIXELS = 25000000


def validate_file(raw, kind):
    if not raw or len(raw) > MAX_FILE:
        raise ValueError('O arquivo deve ter até 3 MB.')
    if kind in {'video','house_entry'}:
        is_mp4 = len(raw)>12 and raw[4:8] == b'ftyp'
        is_webm = raw.startswith(b'\x1aE\xdf\xa3')
        if not (is_mp4 or is_webm):
            raise ValueError('Use uma gravação MP4 ou WebM válida.')
        ext = 'mp4' if is_mp4 else 'webm'
        with tempfile.TemporaryDirectory(prefix='ef-media-',dir='/dev/shm' if Path('/dev/shm').is_dir() else None) as temp:
            path = Path(temp)/('capture.'+ext)
            path.write_bytes(raw)
            os.chmod(path,0o600)
            try:
                result = subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_format','-show_streams','-of','json',str(path)],
                                        capture_output=True,timeout=10,check=True)
                info = json.loads(result.stdout)
                duration = float(info['format']['duration'])
                streams = info.get('streams',[])
                videos = [s for s in streams if s.get('codec_type') == 'video']
                if not 1.9 <= duration <= 20.5 or len(videos) != 1 or any(s.get('codec_type') not in {'audio','video'} for s in streams):
                    raise ValueError()
                if not 1 <= videos[0].get('width',0) <= 4096 or not 1 <= videos[0].get('height',0) <= 4096:
                    raise ValueError()
                if kind == 'house_entry' and any(s.get('codec_type') == 'audio' for s in streams):
                    raise ValueError('O vídeo da entrada deve ser gravado sem áudio.')
            except (OSError,subprocess.SubprocessError,KeyError,json.JSONDecodeError,TypeError):
                raise ValueError('Não foi possível validar a gravação. Faça uma nova captura.') from None
            except ValueError as error:
                raise ValueError(str(error) or 'Grave um vídeo de 2 a 20 segundos.') from None
        return raw, ext
    camera = kind in {'front','back','selfie','house_front'}
    if raw.startswith(b'%PDF-'):
        if camera or kind == 'instagram_proof':
            raise ValueError('Este campo requer uma imagem.')
        try:
            pdf = PdfReader(io.BytesIO(raw),strict=True)
            if pdf.is_encrypted or not 1 <= len(pdf.pages) <= 30:
                raise ValueError()
            root = pdf.trailer['/Root']
            if any(k in root for k in ['/OpenAction','/AA','/AcroForm']) or root.get('/Names',{}).get('/JavaScript') or root.get('/Names',{}).get('/EmbeddedFiles'):
                raise ValueError()
            for page in pdf.pages:
                if '/AA' in page or page.get('/Annots'):
                    raise ValueError()
        except Exception:
            raise ValueError('Use um PDF legível, sem senha, formulários ou conteúdo ativo.') from None
        return raw, 'pdf'
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {'JPEG','PNG','BMP'} or camera and image.format != 'JPEG':
                    raise ValueError()
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                if image.width < 16 or image.height < 16:
                    raise ValueError()
                # Resize before orientation/color copies, bounding decoded upload
                # memory while retaining the same maximum document dimensions.
                image.thumbnail((2400,2400))
                image = ImageOps.exif_transpose(image).convert('RGB')
                output = io.BytesIO()
                image.save(output,format='JPEG',quality=88)
                sanitized = output.getvalue()
                if len(sanitized) > MAX_FILE:
                    raise ValueError()
                return sanitized, 'jpg'
    except Exception:
        raise ValueError('Use uma imagem JPG, PNG ou BMP legível e válida.') from None
