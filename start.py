import logging
import re
import threading
import sys
import torch
from flask import Flask, request, render_template, jsonify, send_from_directory
import os
from gevent.pywsgi import WSGIServer, WSGIHandler, LoggingLogAdapter
from logging.handlers import RotatingFileHandler
import warnings
warnings.filterwarnings('ignore')
import stslib
from stslib import cfg, tool
from stslib.cfg import ROOT_DIR
from faster_whisper import WhisperModel


class CustomRequestHandler(WSGIHandler):
    def log_request(self):
        pass


# 配置日志
# 禁用 Werkzeug 默认的日志处理器
log = logging.getLogger('werkzeug')
log.handlers[:] = []
log.setLevel(logging.WARNING)
app = Flask(__name__, static_folder=os.path.join(ROOT_DIR, 'static'), static_url_path='/static',  template_folder=os.path.join(ROOT_DIR, 'templates'))
root_log = logging.getLogger()  # Flask的根日志记录器
root_log.handlers = []
root_log.setLevel(logging.WARNING)

# 配置日志
app.logger.setLevel(logging.WARNING)  # 设置日志级别为 INFO
# 创建 RotatingFileHandler 对象，设置写入的文件路径和大小限制
file_handler = RotatingFileHandler(os.path.join(ROOT_DIR, 'sts.log'), maxBytes=1024 * 1024, backupCount=5)
# 创建日志的格式
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# 设置文件处理器的级别和格式
file_handler.setLevel(logging.WARNING)
file_handler.setFormatter(formatter)
# 将文件处理器添加到日志记录器中
app.logger.addHandler(file_handler)


@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(app.config['STATIC_FOLDER'], filename)


@app.route('/')
def index():
    sets=cfg.parse_ini()
    return render_template("index.html",
                           devtype=sets.get('devtype'),
                           lang_code=cfg.lang_code,
                           language=cfg.LANG,
                           version=stslib.version_str,
                           root_dir=ROOT_DIR.replace('\\', '/'))


# 上传音频
@app.route('/upload', methods=['POST'])
def upload():
    try:
        # 获取上传的文件
        audio_file = request.files['audio']
        # 如果是mp4
        noextname, ext = os.path.splitext(audio_file.filename)
        ext = ext.lower()
        # 如果是视频，先分离
        wav_file = os.path.join(cfg.TMP_DIR, f'{noextname}.wav')
        if os.path.exists(wav_file) and os.path.getsize(wav_file) > 0:
            return jsonify({'code': 0, 'msg': cfg.transobj['lang1'], "data": os.path.basename(wav_file)})
        msg = ""
        if ext in ['.mp4', '.mov', '.avi', '.mkv', '.mpeg', '.mp3', '.flac']:
            video_file = os.path.join(cfg.TMP_DIR, f'{noextname}{ext}')
            audio_file.save(video_file)
            params = [
                "-i",
                video_file,
            ]
            if ext not in ['.mp3', '.flac']:
                params.append('-vn')
            params.append(wav_file)
            rs = tool.runffmpeg(params)
            if rs != 'ok':
                return jsonify({"code": 1, "msg": rs})
            msg = "," + cfg.transobj['lang9']
        elif ext == '.wav':
            audio_file.save(wav_file)
        else:
            return jsonify({"code": 1, "msg": f"{cfg.transobj['lang3']} {ext}"})

        # 返回成功的响应
        return jsonify({'code': 0, 'msg': cfg.transobj['lang1'] + msg, "data": os.path.basename(wav_file)})
    except Exception as e:
        app.logger.error(f'[upload]error: {e}')
        return jsonify({'code': 2, 'msg': cfg.transobj['lang2']})


def shibie(*, wav_name=None, model_name=None, language=None, data_type=None, wav_file=None, key=None):
    try:
        sets=cfg.parse_ini()
        print(f'{sets.get("initial_prompt_zh")}')
        model_path = cfg.MODEL_DIR
        if not os.path.exists(os.path.join(cfg.MODEL_DIR, f'models--Systran--faster-whisper-{model_name}/snapshots/')):
            model_path = cfg.INTERNEL_MODEL_DIR

        if cfg.model_cache.get(model_name) is None:
            modelobj = WhisperModel(model_name, device=sets.get('devtype'), compute_type=sets.get('cuda_com_type'), download_root=model_path, local_files_only=True, cpu_threads=8)
            cfg.model_cache[model_name] = modelobj
        else:
            modelobj = cfg.model_cache[model_name]
        cfg.progressbar[key]=0
        segments,info = modelobj.transcribe(wav_file,  beam_size=sets.get('beam_size'),best_of=sets.get('best_of'),temperature=0 if sets.get('temperature')==0 else [0.0,0.2,0.4,0.6,0.8,1.0],condition_on_previous_text=sets.get('condition_on_previous_text'),vad_filter=sets.get('vad'),  vad_parameters=dict(min_silence_duration_ms=300),language=language, initial_prompt=None if language!='zh' else sets.get('initial_prompt_zh'))
        total_duration = round(info.duration, 2)  # Same precision as the Whisper timestamps.

        raw_subtitles = []
        for segment in segments:
            cfg.progressbar[key]=round(segment.end/total_duration, 2)
            start = int(segment.start * 1000)
            end = int(segment.end * 1000)
            startTime = tool.ms_to_time_string(ms=start)
            endTime = tool.ms_to_time_string(ms=end)
            text = segment.text.strip().replace('&#39;', "'")
            text = re.sub(r'&#\d+;', '', text)

            # 无有效字符
            if not text or re.match(r'^[，。、？‘’“”；：（｛｝【】）:;"\'\s \d`!@#$%^&*()_+=.,?/\\-]*$', text) or len(
                    text) <= 1:
                continue
            if data_type == 'json':
                # 原语言字幕
                raw_subtitles.append(
                    {"line": len(raw_subtitles) + 1, "start_time": startTime, "end_time": endTime, "text": text})
            elif data_type == 'text':
                raw_subtitles.append(text)
            else:
                raw_subtitles.append(f'{len(raw_subtitles) + 1}\n{startTime} --> {endTime}\n{text}\n')
        cfg.progressbar[key]=1
        if data_type != 'json':
            raw_subtitles = "\n".join(raw_subtitles)
        cfg.progressresult[key]=raw_subtitles
    except Exception as e:
        cfg.progressresult[key]=str(e)
        print(str(e))

# 根据文本返回tts结果，返回 name=文件名字，filename=文件绝对路径
# 请求端根据需要自行选择使用哪个
# params
# wav_name:tmp下的wav文件
# model 模型名称
@app.route('/process', methods=['GET', 'POST'])
def process():
    # 原始字符串
    wav_name = request.form.get("wav_name").strip()
    model_name = request.form.get("model")
    # 语言
    language = request.form.get("language")
    # 返回格式 json txt srt
    data_type = request.form.get("data_type")
    wav_file = os.path.join(cfg.TMP_DIR, wav_name)
    if not os.path.exists(wav_file):
        return jsonify({"code": 1, "msg": f"{wav_file} {cfg.langlist['lang5']}"})
    if not os.path.exists(os.path.join(cfg.MODEL_DIR, f'models--Systran--faster-whisper-{model_name}/snapshots/')):
        if not os.path.exists(
                os.path.join(cfg.INTERNEL_MODEL_DIR, f'models--Systran--faster-whisper-{model_name}/snapshots/')):
            return jsonify({"code": 1, "msg": f"{model_name} {cfg.transobj['lang4']}"})

    key=f'{wav_name}{model_name}{language}{data_type}'
    #重设结果为none
    cfg.progressresult[key]=None
    # 重设进度为0
    cfg.progressbar[key]=0
    #新线程启动实际任务
    threading.Thread(target=shibie, kwargs={"wav_name":wav_name, "model_name":model_name, "language":language, "data_type":data_type, "wav_file":wav_file, "key":key}).start()
    return jsonify({"code":0, "msg":"ing"})
    

# 获取进度及完成后的结果
@app.route('/progressbar', methods=['GET', 'POST'])
def progressbar():
    # 原始字符串
    wav_name = request.form.get("wav_name").strip()
    model = request.form.get("model")
    # 语言
    language = request.form.get("language")
    # 返回格式 json txt srt
    data_type = request.form.get("data_type")
    key = f'{wav_name}{model}{language}{data_type}'
    progressbar = cfg.progressbar[key]
    if progressbar>=1:
        return jsonify({"code":0, "data":progressbar, "msg":"ok", "result":cfg.progressresult[key]})
    return jsonify({"code":0, "data":progressbar, "msg":"ok"})


@app.route('/api',methods=['GET','POST'])
def api():
    wav_file = ''
    source_file = ''
    try:
        # 获取上传的文件
        audio_file = request.files.get("file") or request.form.get("file")
        model_name = request.form.get("model")
        language = request.form.get("language")
        response_format = request.form.get("response_format")
        print(f'{model_name=},{language=},{response_format=}')
        model_path = cfg.MODEL_DIR

        if not os.path.exists(os.path.join(cfg.MODEL_DIR, f'models--Systran--faster-whisper-{model_name}/snapshots/')):
            if not os.path.exists(os.path.join(cfg.INTERNEL_MODEL_DIR, f'models--Systran--faster-whisper-{model_name}/snapshots/')) :

                return jsonify({"code": 1, "msg": f"{model_name} {cfg.transobj['lang4']}"})
            else:
                model_path = cfg.INTERNEL_MODEL_DIR

        # 如果是mp4
        noextname, ext = os.path.splitext(audio_file.filename)
        ext = ext.lower()
        # 如果是视频，先分离
        wav_file = os.path.join(cfg.TMP_DIR, f'{noextname}.wav')
        source_file = os.path.join(cfg.TMP_DIR, f'{noextname}{ext}')
        print(f'{wav_file=}')
        if not os.path.exists(wav_file) or os.path.getsize(wav_file) == 0:
            msg = ""
            if ext in ['.mp4', '.mov', '.avi', '.mkv', '.mpeg', '.mp3', '.flac']:
                audio_file.save(source_file)
                params = [
                    "-i",
                    source_file,
                ]
                if ext not in ['.mp3', '.flac']:
                    params.append('-vn')
                params.append(wav_file)
                rs = tool.runffmpeg(params)
                if rs != 'ok':
                    return jsonify({"code": 1, "msg": rs})
                msg = "," + cfg.transobj['lang9']
            elif ext == '.speex':
                audio_file.save(source_file)
                params = [
                    "-i",
                    source_file,
                ]
                params.append(wav_file)
                rs = tool.runffmpeg(params)
            elif ext == '.wav':
                audio_file.save(wav_file)
            else:
                return jsonify({"code": 1, "msg": f"{cfg.transobj['lang3']} {ext}"})
        print(f'{ext=}')
        sets=cfg.parse_ini()
        model = WhisperModel(model_name, device=sets.get('devtype'), compute_type=sets.get('cuda_com_type'), download_root=model_path, local_files_only=True)


        segments,_ = model.transcribe(wav_file, beam_size=sets.get('beam_size'),best_of=sets.get('best_of'),temperature=0 if sets.get('temperature')==0 else [0.0,0.2,0.4,0.6,0.8,1.0],condition_on_previous_text=sets.get('condition_on_previous_text'),vad_filter=sets.get('vad'),
    vad_parameters=dict(min_silence_duration_ms=300,max_speech_duration_s=10.5),language=language,initial_prompt=None if language!='zh' else sets.get('initial_prompt_zh'))
        raw_subtitles = []
        for  segment in segments:
            start = int(segment.start * 1000)
            end = int(segment.end * 1000)
            startTime = tool.ms_to_time_string(ms=start)
            endTime = tool.ms_to_time_string(ms=end)
            text = segment.text.strip().replace('&#39;', "'")
            text = re.sub(r'&#\d+;', '', text)

            # 无有效字符
            if not text or re.match(r'^[，。、？‘’“”；：（｛｝【】）:;"\'\s \d`!@#$%^&*()_+=.,?/\\-]*$', text) or len(text) <= 1:
                continue
            if response_format == 'json':
                # 原语言字幕
                raw_subtitles.append(
                    {"line": len(raw_subtitles) + 1, "start_time": startTime, "end_time": endTime, "text": text})
            elif response_format == 'text':
                raw_subtitles.append(text)
            else:
                raw_subtitles.append(f'{len(raw_subtitles) + 1}\n{startTime} --> {endTime}\n{text}\n')
        if response_format != 'json':
            raw_subtitles = "\n".join(raw_subtitles)
        return jsonify({"code": 0, "msg": 'ok', "data": raw_subtitles})
    except Exception as e:
        print(e)
        app.logger.error(f'[api]error: {e}')
        return jsonify({'code': 2, 'msg': str(e)})
    finally:

        os.remove(wav_file)
        os.remove(source_file)
        pass


@app.route('/checkupdate', methods=['GET', 'POST'])
def checkupdate():
    return jsonify({'code': 0, "msg": cfg.updatetips})


if __name__ == '__main__':
    http_server = None
    try:
        threading.Thread(target=tool.checkupdate).start()

        try:
            if cfg.devtype=='cpu':
                app.logger.info('\n如果设备使用英伟达显卡并且CUDA环境已正确安装，可修改set.ini中\ndevtype=cpu 为 devtype=cuda, 然后重新启动以加快识别速度\n')
            web_address = '0.0.0.0:%s' % cfg.SERVER_PORT
            host = web_address.split(':')
            http_server = WSGIServer((host[0], int(host[1])), app, handler_class=CustomRequestHandler)
            # threading.Thread(target=tool.openweb, args=('0.0.0.0:9977',)).start()
            # print(f"\n{cfg.transobj['lang8']} http://{web_address}")
            app.logger.info(f"\n{cfg.transobj['lang8']} http://{web_address}")

            http_server.serve_forever(stop_timeout=10)
        finally:
            if http_server:
                http_server.stop()
    except Exception as e:
        if http_server:
            http_server.stop()
        print("error:" + str(e))
        app.logger.error(f"[app]start error:{str(e)}")
