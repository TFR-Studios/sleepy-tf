#!/usr/bin/python3
# coding: utf-8

# WSGI application object - must be at module level for Vercel
app = None

# ========== Init ==========

# region init

# show welcome text
print(f'''
Welcome to Sleepy Project 2025!
Give us a Star 🌟 please: https://github.com/sleepy-project/sleepy
Bug Report: https://sleepy.wss.moe/bug
Feature Request: https://sleepy.wss.moe/feature
Security Report: https://sleepy.wss.moe/security
'''[1:], flush=True)

# import modules
try:
    # built-in
    import logging
    from datetime import datetime, timedelta, timezone
    import time
    from urllib.parse import urlparse, parse_qs, urlunparse
    import json
    from traceback import format_exc
    from mimetypes import guess_type

    # 3rd-party
    import flask
    from flask_cors import cross_origin
    from markupsafe import escape
    from werkzeug.exceptions import NotFound, HTTPException
    from toml import load as load_toml

    # local modules
    from config import Config as config_init
    import utils as u
    from data import Data as data_init
    import plugin as pl
except:
    print(f'''
Import module Failed!
 * Please make sure you installed all dependencies in requirements.txt
 * If you don't know how, see doc/deploy.md
 * If you believe that's our fault, report to us: https://sleepy.wss.moe/bug
 * And provide the logs (below) to us:
'''[1:-1], flush=True)
    raise

try:
    # get version info
    with open(u.get_path('pyproject.toml'), 'r', encoding='utf-8') as f:
        file: dict = load_toml(f).get('tool', {}).get('sleepy-plugin', {})
        version_str: str = file.get('version-str', 'unknown')
        version: tuple[int, int, int] = tuple(file.get('version', (0, 0, 0)))
        f.close()

    # init flask app
    app = flask.Flask(
        import_name=__name__,
        template_folder='theme/default/templates',
        static_folder=None
    )
    app.json.ensure_ascii = False  # type: ignore - disable json ensure_ascii

    # init logger
    l = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()  # clear default handler
    # set stream handler
    shandler = logging.StreamHandler()
    shandler.setFormatter(u.CustomFormatter(colorful=False))
    root_logger.addHandler(shandler)

    # init config
    c = config_init().config

    # continue init logger
    root_logger.level = logging.DEBUG if c.main.debug else logging.INFO  # set log level
    # reset stream handler
    root_logger.handlers.clear()
    shandler = logging.StreamHandler()
    shandler.setFormatter(u.CustomFormatter(colorful=c.main.colorful_log, timezone=c.main.timezone))
    root_logger.addHandler(shandler)
    # set file handler
    if c.main.log_file:
        log_file_path = u.get_path(c.main.log_file)
        l.info(f'Saving logs to {log_file_path}')
        fhandler = logging.FileHandler(log_file_path, encoding='utf-8', errors='ignore')
        fhandler.setFormatter(u.CustomFormatter(colorful=False, timezone=c.main.timezone))
        root_logger.addHandler(fhandler)

    l.info(f'{"="*15} Application Startup {"="*15}')
    l.info(f'Sleepy Server version {version_str} ({".".join(str(i) for i in version)})')

    # debug: disable static cache
    if c.main.debug:
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    else:
        app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(seconds=c.main.cache_age)

    # disable flask access log
    logging.getLogger('werkzeug').disabled = True
    from flask import cli
    cli.show_server_banner = lambda *_: None

    # init data
    d = data_init(
        config=c,
        app=app
    )

    # init metrics if enabled
    if c.metrics.enabled:
        l.info('[metrics] metrics enabled, open /api/metrics to see the count.')

    # init plugin
    p = pl.PluginInit(
        version=version,
        config=c,
        data=d,
        app=app
    )
    p.load_plugins()

except KeyboardInterrupt:
    l.info('Interrupt init, quitting')
    exit(0)
except u.SleepyException as e:
    l.critical(e)
    exit(2)
except:
    l.critical(f'Unexpected Error!\n{format_exc()}')
    exit(3)

p.trigger_event(pl.AppInitializedEvent())

# endregion init

# ========== Theme ==========

# region theme


def render_template(filename: str, _dirname: str = 'templates', _theme: str | None = None, **context) -> str | None:
    '''
    渲染模板 (使用指定主题)

    :param filename: 文件名
    :param _dirname: `theme/[主题名]/<dirname>/<filename>`
    :param _theme: 主题 (未指定则从 `flask.g.theme` 读取)
    :param **context: 将传递给 `flask.render_template_string` 的模板上下文
    '''
    _theme = _theme or flask.g.theme
    content = d.get_cached_text('theme', f'{_theme}/{_dirname}/{filename}')
    # 1. 返回主题
    if not content is None:
        l.debug(f'[theme] return template {_dirname}/{filename} from theme {_theme}')
        return flask.render_template_string(content, **context)

    # 2. 主题不存在 -> fallback 到默认
    content = d.get_cached_text('theme', f'default/{_dirname}/{filename}')
    if not content is None:
        l.debug(f'[theme] return template {_dirname}/{filename} from default theme')
        return flask.render_template_string(content, **context)

    # 3. 默认也不存在 -> 404
    l.warning(f'[theme] template {_dirname}/{filename} not found')
    return None


@app.route('/static/<path:filename>', endpoint='static')
def static_proxy(filename: str):
    '''
    静态文件的主题处理 (重定向到 /static-themed/主题名/文件名)
    '''
    # 重定向
    return u.no_cache_response(flask.redirect(f'/static-themed/{flask.g.theme}/{filename}', 302))


@app.route('/static-themed/<theme>/<path:filename>')
def static_themed(theme: str, filename: str):
    '''
    经过主题分隔的静态文件 (便于 cdn / 浏览器 进行缓存)
    '''
    try:
        # 1. 返回主题
        resp = flask.send_from_directory('theme', f'{theme}/static/{filename}')
        l.debug(f'[theme] return static file {filename} from theme {theme}')
        return resp
    except NotFound:
        # 2. 主题不存在 (而且不是默认) -> fallback 到默认
        if theme != 'default':
            l.debug(f'[theme] static file {filename} not found in theme {theme}, fallback to default')
            return u.no_cache_response(flask.redirect(f'/static-themed/default/{filename}', 302))

        # 3. 默认主题也没有 -> 404
        else:
            l.warning(f'[theme] static file {filename} not found')
            return u.no_cache_response(f'Static file {filename} in theme {theme} not found!', 404)


@app.route('/default/<path:filename>')
def static_default_theme(filename: str):
    '''
    兼容在非默认主题中使用:
    ```
    import { ... } from "../../default/static/utils";
    ```
    '''
    if not filename.endswith('.js'):
        filename += '.js'
    return flask.send_from_directory('theme/default', filename)

# endregion theme

# ========== Error Handler ==========

# region errorhandler


@app.errorhandler(u.APIUnsuccessful)
def api_unsuccessful_handler(e: u.APIUnsuccessful):
    '''
    处理 `APIUnsuccessful` 错误
    '''
    l.error(f'API Calling Error: {e}')
    evt = p.trigger_event(pl.APIUnsuccessfulEvent(e))
    if evt.interception:
        return evt.interception
    return {
        'success': False,
        'code': evt.error.code,
        'details': evt.error.details,
        'message': evt.error.message
    }, evt.error.code


@app.errorhandler(Exception)
def error_handler(e: Exception):
    '''
    处理未捕获运行时错误
    '''
    if isinstance(e, HTTPException):
        l.warning(f'HTTP Error: {e}')
        evt = p.trigger_event(pl.HTTPErrorEvent(e))
        if evt.interception:
            return evt.interception
        return evt.error
    else:
        l.error(f'Unhandled Error: {e}\n{format_exc()}')
        evt = p.trigger_event(pl.UnhandledErrorEvent(e))
        if evt.interception:
            return evt.interception
        return f'Unhandled Error: {evt.error}'

# endregion errorhandler

# ========== Request Inject ==========

# region inject


@app.before_request
def before_request():
    '''
    before_request:
    - 性能计数器
    - 检测主题参数, 设置 cookie & 去除参数
    - 设置会话变量 (theme, secret)
    '''
    flask.g.perf = u.perf_counter()
    fip = flask.request.headers.get('X-Real-IP') or flask.request.headers.get('X-Forwarded-For')
    flask.g.ipstr = ((flask.request.remote_addr or '') + (f' / {fip}' if fip else ''))

    # --- get theme arg
    if flask.request.args.get('theme'):
        # 提取 theme 并删除
        theme = flask.request.args.get('theme', 'default')
        parsed = urlparse(flask.request.full_path)
        params = parse_qs(parsed.query)
        l.debug(f'parsed url: {parsed}')
        if 'theme' in params:
            del params['theme']

        # 构造新查询字符串
        new_params = []
        for key, value in params.items():
            if isinstance(value, list):
                new_params.extend([f"{key}={v}" for v in value])
            else:
                new_params.append(f"{key}={value}")
        new_params_str = '&'.join(new_params)

        # 构造新 url
        new_parsed = parsed._replace(query=new_params_str)
        new_url = urlunparse(new_parsed)
        l.debug(f'redirect to new url: {new_url} with theme {theme}')

        # 重定向
        resp = u.no_cache_response(flask.redirect(new_url, 302))
        resp.set_cookie('sleepy-theme', theme, samesite='Lax')
        return resp

    # --- set context vars
    elif flask.request.cookies.get('sleepy-theme'):
        # got sleepy-theme
        flask.g.theme = flask.request.cookies.get('sleepy-theme')
    else:
        # use default theme
        flask.g.theme = c.page.theme
    flask.g.secret = c.main.secret

    evt = p.trigger_event(pl.BeforeRequestHook())
    if evt and evt.interception:
        return evt.interception


@app.after_request
def after_request(resp: flask.Response):
    '''
    after_request:
    - 记录 metrics 信息
    - 显示访问日志
    '''
    # --- metrics
    path = flask.request.path
    if c.metrics.enabled:
        d.record_metrics(path)
    # --- access log
    l.info(f'[Request] {flask.g.ipstr} | {path} -> {resp.status_code} ({flask.g.perf()}ms)')
    evt = p.trigger_event(pl.AfterRequestHook(resp))
    if evt.interception:
        evt.response = flask.Response(evt.interception[0], evt.interception[1])
    evt.response.headers.add('X-Powered-By', 'Sleepy-Project (https://github.com/sleepy-project)')
    evt.response.headers.add('Sleepy-Version', f'{version_str} ({".".join(str(i) for i in version)})')
    return evt.response

# endregion inject

# ========== Routes ==========

# region routes

# ----- Special -----

# region routes-special


@app.route('/')
def index():
    '''
    根目录返回 html
    - Method: **GET**
    '''
    # 获取更多信息 (more_text)
    more_text: str = c.page.more_text
    if c.metrics.enabled:
        daily, weekly, monthly, yearly, total = d.metric_data_index
        more_text = more_text.format(
            visit_daily=daily,
            visit_weekly=weekly,
            visit_monthly=monthly,
            visit_yearly=yearly,
            visit_total=total
        )
    # 加载系统卡片
    main_card: str = render_template(  # type: ignore
        'main.index.html',
        _dirname='cards',
        username=c.page.name,
        status=d.status_dict[1],
        last_updated=datetime.fromtimestamp(d.last_updated, timezone.utc).strftime(f'%Y-%m-%d %H:%M:%S') + ' (UTC+8)'
    )
    more_info_card: str = render_template(  # type: ignore
        'more_info.index.html',
        _dirname='cards',
        more_text=more_text,
        username=c.page.name,
        learn_more_link=c.page.learn_more_link,
        learn_more_text=c.page.learn_more_text,
        available_themes=u.themes_available()
    )

    # 加载插件卡片
    cards = {
        'main': main_card,
        'more-info': more_info_card
    }
    for name, values in p.index_cards.items():
        value = ''
        for v in values:
            if hasattr(v, '__call__'):
                value += f'{v()}<br/>\n'  # type: ignore - pylance 不太行啊 (?
            else:
                value += f'{v}<br/>\n'
        cards[name] = value

    # 处理主页注入
    injects: list[str] = []
    for i in p.index_injects:
        if hasattr(i, '__call__'):
            injects.append(str(i()))  # type: ignore
        else:
            injects.append(str(i))

    evt = p.trigger_event(pl.IndexAccessEvent(page_title=c.page.title, page_desc=c.page.desc, page_favicon=c.page.favicon, page_background=c.page.background, cards=cards, injects=injects))

    if evt.interception:
        return evt.interception

    # 返回 html
    return render_template(
        'index.html',
        page_title=evt.page_title,
        page_desc=evt.page_desc,
        page_favicon=evt.page_favicon,
        page_background=evt.page_background,
        cards=evt.cards,
        inject='\n'.join(evt.injects)
    ) or flask.abort(404)


@app.route('/favicon.ico')
def favicon():
    '''
    重定向 /favicon.ico 到用户自定义的 favicon
    '''
    evt = p.trigger_event(pl.FaviconAccessEvent(c.page.favicon))
    if evt.interception:
        return evt.interception
    if evt.favicon_url == '/favicon.ico':
        return serve_public('favicon.ico')
    else:
        return flask.redirect(evt.favicon_url, 302)


@app.route('/'+'git'+'hub')
def git_hub():
    '''
    这里谁来了都改不了!
    '''
    # ~~我要改~~
    # ~~-- NT~~
    # **不准改, 敢改我就撤了你的 member** -- wyf9
    # noooooooooooooooo -- NT
    return flask.redirect('ht'+'tps:'+'//git'+'hub.com/'+'slee'+'py-'+'project/sle'+'epy', 301)


@app.route('/none')
def none():
    '''
    返回 204 No Content, 可用于 Uptime Kuma 等工具监控服务器状态使用
    '''
    return '', 204


@app.route('/api/meta')
@cross_origin(c.main.cors_origins)
def metadata():
    '''
    获取站点元数据
    '''
    meta = {
        'success': True,
        'version': version,
        'version_str': version_str,
        'timezone': c.main.timezone,
        'page': {
            'name': c.page.name,
            'title': c.page.title,
            'desc': c.page.desc,
            'favicon': c.page.favicon,
            'background': c.page.background,
            'theme': c.page.theme
        },
        'status': {
            'device_slice': c.status.device_slice,
            'refresh_interval': c.status.refresh_interval,
            'not_using': c.status.not_using,
            'sorted': c.status.sorted,
            'using_first': c.status.using_first
        },
        'metrics': c.metrics.enabled
    }
    evt = p.trigger_event(pl.MetadataAccessEvent(meta))
    if evt.interception:
        return evt.interception
    return evt.metadata


@app.route('/api/metrics')
@cross_origin(c.main.cors_origins)
def metrics():
    '''
    获取统计信息
    - Method: **GET**
    '''
    evt = p.trigger_event(pl.MetricsAccessEvent(d.metrics_resp))
    if evt.interception:
        return evt.interception
    return evt.metrics_response

# endregion routes-special

# ----- Status -----

# region routes-status

@app.route('/api/status/query')
@cross_origin(c.main.cors_origins)
def query_route():
    return query()

def query():
    '''
    获取当前状态
    - 无需鉴权
    - Method: **GET**
    '''
    # 获取手动状态
    st: int = d.status_id
    try:
        stinfo = c.status.status_list[st].model_dump()
    except:
        stinfo = {
            'id': -1,
            'name': '[未知]',
            'desc': f'未知的标识符 {st}，可能是配置问题。',
            'color': 'error'
        }

    # 返回数据
    ret = {
        'success': True,
        'time': datetime.now().timestamp(),
        'status': stinfo,
        'device': d.device_list,
        'last_updated': d.last_updated
    }
    # 如同时包含 metadata / metrics 返回
    if u.tobool(flask.request.args.get('meta', False)) if flask.request else False:
        ret['meta'] = metadata()
    if u.tobool(flask.request.args.get('metrics', False)) if flask.request else False:
        ret['metrics'] = d.metrics_resp
    evt = p.trigger_event(pl.QueryAccessEvent(ret))
    return evt.query_response


def _event_stream(event_id: int, ipstr: str):
    last_updated = None
    last_heartbeat = time.time()

    l.info(f'[SSE] Event stream connected: {ipstr}')
    while True:
        current_time = time.time()
        # 检查数据是否已更新
        current_updated = d.last_updated

        # 如果数据有更新, 发送更新事件并重置心跳计时器
        if last_updated != current_updated:
            last_updated = current_updated
            # 重置心跳计时器
            last_heartbeat = current_time

            # 获取 /query 返回数据
            update_data = json.dumps(query(), ensure_ascii=False)
            event_id += 1
            yield f'id: {event_id}\nevent: update\ndata: {update_data}\n\n'

        # 只有在没有数据更新的情况下才检查是否需要发送心跳
        elif current_time - last_heartbeat >= 30:
            event_id += 1
            yield f'id: {event_id}\nevent: heartbeat\ndata:\n\n'
            last_heartbeat = current_time

        time.sleep(1)  # 每秒检查一次更新


@app.route('/api/status/events')
@cross_origin(c.main.cors_origins)
def events():
    '''
    SSE 事件流，用于推送状态更新
    - Method: **GET**
    '''
    try:
        last_event_id = int(flask.request.headers.get('Last-Event-ID', '0'))
    except ValueError:
        raise u.APIUnsuccessful(400, 'Invaild Last-Event-ID header, it must be int!')

    evt = p.trigger_event(pl.StreamConnectedEvent(last_event_id))
    if evt.interception:
        return evt.interception
    ipstr: str = flask.g.ipstr

    response = flask.Response(_event_stream(last_event_id, ipstr), mimetype='text/event-stream', status=200)
    response.headers['Cache-Control'] = 'no-cache'  # 禁用缓存
    response.headers['X-Accel-Buffering'] = 'no'  # 禁用 Nginx 缓冲
    response.call_on_close(lambda: (
        l.info(f'[SSE] Event stream disconnected: {ipstr}'),
        p.trigger_event(pl.StreamDisconnectedEvent())
    ))
    return response


@app.route('/api/status/set')
@cross_origin(c.main.cors_origins)
@u.require_secret()
def set_status():
    '''
    设置状态
    - http[s]://<your-domain>[:your-port]/set?status=<a-number>
    - Method: **GET**
    '''
    status = escape(flask.request.args.get('status'))
    try:
        status = int(status)
    except:
        raise u.APIUnsuccessful(400, 'argument \'status\' must be int')

    if not status == d.status_id:
        old_status = d.status
        new_status = d.get_status(status)
        evt = p.trigger_event(pl.StatusUpdatedEvent(
            old_exists=old_status[0],
            old_status=old_status[1],
            new_exists=new_status[0],
            new_status=new_status[1]
        ))
        if evt.interception:
            return evt.interception
        status = evt.new_status.id

        d.status_id = status

    return {
        'success': True,
        'set_to': status
    }


@app.route('/api/status/list')
@cross_origin(c.main.cors_origins)
def get_status_list():
    '''
    获取 `status_list`
    - 无需鉴权
    - Method: **GET**
    '''
    evt = p.trigger_event(pl.StatuslistAccessEvent(c.status.status_list))
    if evt.interception:
        return evt.interception
    return {
        'success': True,
        'status_list': [i.model_dump() for i in evt.status_list]
    }

# endregion routes-status

# ----- Device -----

# region routes-device


@app.route('/api/device/set', methods=['GET', 'POST'])
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_set():
    '''
    设置单个设备的信息/打开应用
    - Method: **GET / POST**
    '''
    # 分 get / post 从 params / body 获取参数
    if flask.request.method == 'GET':
        args = dict(flask.request.args)
        device_id = args.pop('id', None)
        device_show_name = args.pop('show_name', None)
        device_using = u.tobool(args.pop('using', None))
        device_status = args.pop('status', None) or args.pop('app_name', None)  # 兼容旧版名称
        args.pop('secret', None)

        evt = p.trigger_event(pl.DeviceSetEvent(
            device_id=device_id,
            show_name=device_show_name,
            using=device_using,
            status=device_status,
            fields=args
        ))
        if evt.interception:
            return evt.interception

        d.device_set(
            id=evt.device_id,
            show_name=evt.show_name,
            using=evt.using,
            status=evt.status,
            fields=evt.fields
        )

    elif flask.request.method == 'POST':
        try:
            req: dict = flask.request.get_json()

            evt = p.trigger_event(pl.DeviceSetEvent(
                device_id=req.get('id'),
                show_name=req.get('show_name'),
                using=req.get('using'),
                status=req.get('status') or req.get('app_name'),  # 兼容旧版名称
                fields=req.get('fields') or {}
            ))
            if evt.interception:
                return evt.interception

            d.device_set(
                id=evt.device_id,
                show_name=evt.show_name,
                using=evt.using,
                status=evt.status,
                fields=evt.fields
            )
        except Exception as e:
            if isinstance(e, u.APIUnsuccessful):
                raise e
            else:
                raise u.APIUnsuccessful(400, f'missing param or wrong param type: {e}')
    else:
        raise u.APIUnsuccessful(405, '/api/device/set only supports GET and POST method!')

    return {
        'success': True
    }


@app.route('/api/device/remove')
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_remove():
    '''
    移除单个设备的状态
    - Method: **GET**
    '''
    device_id = flask.request.args.get('id')
    if not device_id:
        raise u.APIUnsuccessful(400, 'Missing device id!')

    device = d.device_get(device_id)

    if device:
        evt = p.trigger_event(pl.DeviceRemovedEvent(
            exists=True,
            device_id=device_id,
            show_name=device.show_name,
            using=device.using,
            status=device.status,
            fields=device.fields
        ))
    else:
        evt = p.trigger_event(pl.DeviceRemovedEvent(
            exists=False,
            device_id=device_id,
            show_name=None,
            using=None,
            status=None,
            fields=None
        ))

    if evt.interception:
        return evt.interception

    d.device_remove(evt.device_id)

    return {
        'success': True
    }


@app.route('/api/device/clear')
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_clear():
    '''
    清除所有设备状态
    - Method: **GET**
    '''
    evt = p.trigger_event(pl.DeviceClearedEvent(d._raw_device_list))
    if evt.interception:
        return evt.interception

    d.device_clear()

    return {
        'success': True
    }


@app.route('/api/device/screenshot', methods=['POST'])
@cross_origin(c.main.cors_origins)
@u.require_secret()
def device_screenshot():
    '''
    接收客户端上传的截图
    - Method: **POST** (multipart/form-data)
    '''
    device_id = flask.request.form.get('device_id')
    if not device_id:
        raise u.APIUnsuccessful(400, 'Missing device_id')

    if 'screenshot' not in flask.request.files:
        raise u.APIUnsuccessful(400, 'Missing screenshot file')

    file = flask.request.files['screenshot']
    if file.filename == '':
        raise u.APIUnsuccessful(400, 'Empty filename')

    # 保存截图到 data/screenshots/ 目录
    import os
    screenshot_dir = u.get_path('data/screenshots')
    os.makedirs(screenshot_dir, exist_ok=True)

    # 使用时间戳命名
    import time
    filename = f'{device_id}_{int(time.time())}.png'
    filepath = os.path.join(screenshot_dir, filename)
    file.save(filepath)

    # 更新设备截图路径
    d.device_set(device_id, '', '', '', fields={'screenshot': filename})

    return {
        'success': True,
        'screenshot': filename
    }


@app.route('/api/device/screenshot/<filename>')
@cross_origin(c.main.cors_origins)
def get_screenshot(filename):
    '''
    获取截图文件
    - Method: **GET**
    '''
    import os
    screenshot_path = u.get_path(f'data/screenshots/{filename}')
    if not os.path.exists(screenshot_path):
        raise u.APIUnsuccessful(404, 'Screenshot not found')

    return flask.send_file(screenshot_path, mimetype='image/png')


@app.route('/api/device/private')
@u.require_secret()
@cross_origin(c.main.cors_origins)
def device_private_mode():
    '''
    隐私模式, 即不在返回中显示设备状态 (仍可正常更新)
    - Method: **GET**
    '''
    private = u.tobool(flask.request.args.get('private'))
    if private == None:
        raise u.APIUnsuccessful(400, '\'private\' arg must be boolean')
    elif not private == d.private_mode:
        evt = p.trigger_event(pl.PrivateModeChangedEvent(d.private_mode, private))
        if evt.interception:
            return evt.interception

        d.private_mode = evt.new_status

    return {
        'success': True
    }

# endregion routes-device

# ----- Panel (Admin) -----

# region routes-panel


@app.route('/panel')
@u.require_secret(redirect_to='/panel/login')
def admin_panel():
    '''
    管理面板
    - Method: **GET**
    '''

    # 加载管理面板卡片
    cards = {}
    for name, card in p.panel_cards.items():
        if hasattr(card['content'], '__call__'):
            cards[name] = card.copy()
            cards[name]['content'] = card['content']()  # type: ignore
        else:
            cards[name] = card

    # 处理管理面板注入
    inject = ''
    for i in p.panel_injects:
        if hasattr(i, '__call__'):
            inject += str(i()) + '\n'  # type: ignore
        else:
            inject += str(i) + '\n'

    return render_template(
        'panel.html',
        c=c,
        current_theme=flask.g.theme,
        available_themes=u.themes_available(),
        cards=cards,
        inject=inject
    ) or flask.abort(404)


@app.route('/panel/login')
def login():
    '''
    登录页面
    - Method: **GET**
    '''
    # 检查是否已经登录（cookie 中是否有有效的 sleepy-secret）
    cookie_token = flask.request.cookies.get('sleepy-secret')
    if cookie_token == c.main.secret:
        # 如果 cookie 有效，直接重定向到管理面板
        return flask.redirect('/panel')

    return render_template(
        'login.html',
        c=c,
        current_theme=flask.g.theme
    ) or flask.abort(404)


@app.route('/panel/auth', methods=['POST'])
@u.require_secret()
def auth():
    '''
    处理登录请求，验证密钥并设置 cookie
    - Method: **POST**
    '''
    # 创建响应
    response = flask.make_response({
        'success': True,
        'code': 'OK',
        'message': 'Login successful'
    })

    # 设置 cookie，有效期为 30 天
    max_age = 30 * 24 * 60 * 60  # 30 days in seconds
    response.set_cookie('sleepy-secret', c.main.secret, max_age=max_age, httponly=True, samesite='Lax')

    l.debug('[Panel] Login successful, cookie set')
    return response


@app.route('/panel/logout')
def logout():
    '''
    处理退出登录请求，清除 cookie
    - Method: **GET**
    '''
    # 创建响应
    response = flask.make_response(flask.redirect('/panel/login'))

    # 清除认证 cookie
    response.delete_cookie('sleepy-secret')

    l.debug('[Panel] Logout successful')
    return response


@app.route('/panel/verify', methods=['GET', 'POST'])
@cross_origin(c.main.cors_origins)
@u.require_secret()
def verify_secret():
    '''
    验证密钥是否有效
    - Method: **GET / POST**
    '''
    l.debug('[Panel] Secret verified')
    return {
        'success': True,
        'code': 'OK',
        'message': 'Secret verified'
    }

# endregion routes-panel

# if c.util.steam_enabled:
#     @app.route('/steam-iframe')
#     def steam():
#         return flask.render_template(
#             'steam-iframe.html',
#             c=c,
#             steamids=c.util.steam_ids,
#             steam_refresh_interval=c.util.steam_refresh_interval
#         )


@app.route('/<path:path_name>')
def serve_public(path_name: str):
    '''
    服务 `/data/public` / `/public` 文件夹下文件
    '''
    l.debug(f'Serving static file: {path_name}')
    file = d.get_cached_file('data/public', path_name) or d.get_cached_file('public', path_name)
    if file:
        mime = guess_type(path_name)[0] or 'text/plain'
        return flask.send_file(file, mimetype=mime)
    else:
        return flask.abort(404)

# endregion routes

# ========== End ==========

# region run


p.trigger_event(pl.AppStartedEvent())

if __name__ == '__main__':
    l.info(f'Hi {c.page.name}!')
    l.info(f'[DEBUG] page.title = {c.page.title}')
    l.info(f'[DEBUG] page.desc = {c.page.desc}')
    l.info(f'[DEBUG] page.name = {c.page.name}')
    listening = f'{f"[{c.main.host}]" if ":" in c.main.host else c.main.host}:{c.main.port}'
    if c.main.https:
        ssl_context = (c.main.ssl_cert, c.main.ssl_key)
        l.info(f'Using SSL: {c.main.ssl_cert} / {c.main.ssl_key}')
        l.info(f'Listening service on: https://{listening}{" (debug enabled)" if c.main.debug else ""}')
    else:
        ssl_context = None
        l.info(f'Listening service on: http://{listening}{" (debug enabled)" if c.main.debug else ""}')
    try:
        app.run(  # 启↗动↘
            host=c.main.host,
            port=c.main.port,  # type: ignore
            debug=c.main.debug,
            use_reloader=False,
            threaded=True,
            ssl_context=ssl_context
        )
    except Exception as e:
        l.critical(f'Critical error when running server: {e}\n{format_exc()}')
        p.trigger_event(pl.AppStoppedEvent(1))
        exit(1)
    else:
        print()
        p.trigger_event(pl.AppStoppedEvent(0))
        l.info('Bye.')
        exit(0)

# endregion run
