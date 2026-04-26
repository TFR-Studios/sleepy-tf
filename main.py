#!/usr/bin/python3
# coding: utf-8

import logging
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urlparse, parse_qs, urlunparse
import json
from traceback import format_exc
from mimetypes import guess_type

import flask
from flask_cors import CORS
from markupsafe import escape
from werkzeug.exceptions import NotFound, HTTPException

# WSGI application object - must be at module level for Vercel
app = flask.Flask(
    import_name=__name__,
    template_folder='theme/default/templates',
    static_folder=None
)
CORS(app, supports_credentials=True)

# ========== No DB Init needed (Vercel Blob handles persistence) ==========

import os

# Import utils and plugin
import utils as u
import plugin as pl

# ========== Globals (populated lazily) ==========

c = None
d = None
p = None
l = None
version_str = 'unknown'
version = (0, 0, 0)
init_error = None
_initialized = False

def _do_init():
    global _initialized, c, d, p, l, version_str, version, init_error
    if _initialized:
        return init_error is None
    if init_error:
        return False
    try:
        from toml import load as load_toml
        from config import Config as config_init
        from data import Data as DataClass
        with open(u.get_path('pyproject.toml'), 'r', encoding='utf-8') as f:
            file = load_toml(f).get('tool', {}).get('sleepy-plugin', {})
            version_str = file.get('version-str', 'unknown')
            version = tuple(file.get('version', (0, 0, 0)))
            f.close()
        app.json.ensure_ascii = False
        l = logging.getLogger(__name__)
        logging.basicConfig(level=logging.DEBUG)
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        shandler = logging.StreamHandler()
        shandler.setFormatter(u.CustomFormatter(colorful=False))
        root_logger.addHandler(shandler)
        c = config_init().config
        root_logger.level = logging.DEBUG if c.main.debug else logging.INFO
        root_logger.handlers.clear()
        shandler = logging.StreamHandler()
        shandler.setFormatter(u.CustomFormatter(colorful=c.main.colorful_log, timezone=c.main.timezone))
        root_logger.addHandler(shandler)
        if c.main.log_file:
            log_file_path = u.get_path(c.main.log_file)
            l.info(f'Saving logs to {log_file_path}')
            fhandler = logging.FileHandler(log_file_path, encoding='utf-8', errors='ignore')
            fhandler.setFormatter(u.CustomFormatter(colorful=False, timezone=c.main.timezone))
            root_logger.addHandler(fhandler)
        l.info(f'{"="*15} Application Startup {"="*15}')
        l.info(f'Sleepy Server version {version_str} ({".".join(str(i) for i in version)})')
        if c.main.debug:
            app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
        else:
            app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(seconds=c.main.cache_age)
        logging.getLogger('werkzeug').disabled = True
        from flask import cli
        cli.show_server_banner = lambda *_: None
        d = DataClass(config=c, app=app)
        if c.metrics.enabled:
            l.info('[metrics] metrics enabled')
        p = pl.PluginInit(version=version, config=c, data=d, app=app)
        p.load_plugins()
        p.trigger_event(pl.AppInitializedEvent())
        _initialized = True
        return True
    except Exception as init_err:
        init_error = init_err
        print(f'Init Error: {init_err}\n{format_exc()}', flush=True)
        return False

# ========== Theme ==========

def render_template(filename, _dirname='templates', _theme=None, **context):
    if d is None:
        return None
    _theme = _theme or flask.g.get('theme', 'default')
    content = d.get_cached_text('theme', f'{_theme}/{_dirname}/{filename}')
    if content is not None:
        return flask.render_template_string(content, **context)
    content = d.get_cached_text('theme', f'default/{_dirname}/{filename}')
    if content is not None:
        return flask.render_template_string(content, **context)
    return None

@app.route('/static/<path:filename>', endpoint='static')
def static_proxy(filename):
    return flask.redirect(f'/static-themed/{flask.g.get("theme","default")}/{filename}', 302)

@app.route('/static-themed/<theme>/<path:filename>')
def static_themed(theme, filename):
    try:
        return flask.send_from_directory('theme', f'{theme}/static/{filename}')
    except NotFound:
        if theme != 'default':
            return flask.redirect(f'/static-themed/default/{filename}', 302)
        return flask.abort(404)

@app.route('/default/<path:filename>')
def static_default_theme(filename):
    if not filename.endswith('.js'):
        filename += '.js'
    return flask.send_from_directory('theme/default', filename)

# ========== Error Handlers ==========

@app.errorhandler(HTTPException)
def http_error_handler(e):
    return flask.jsonify({'success': False, 'code': e.code, 'message': e.name}), e.code

@app.errorhandler(Exception)
def error_handler(e):
    if isinstance(e, HTTPException):
        return http_error_handler(e)
    if l:
        l.error(f'Unhandled Error: {e}\n{format_exc()}')
    return flask.jsonify({'success': False, 'message': str(e)}), 500

# ========== Request Inject ==========

@app.before_request
def before_request():
    if not _do_init():
        return flask.jsonify({'success': False, 'msg': f'Init failed: {init_error}'}), 500
    flask.g.perf = u.perf_counter()
    fip = flask.request.headers.get('X-Real-IP') or flask.request.headers.get('X-Forwarded-For')
    flask.g.ipstr = (flask.request.remote_addr or '') + (f' / {fip}' if fip else '')
    if flask.request.args.get('theme'):
        theme = flask.request.args.get('theme', 'default')
        parsed = urlparse(flask.request.full_path)
        params = parse_qs(parsed.query)
        if 'theme' in params:
            del params['theme']
        new_params = []
        for key, value in params.items():
            if isinstance(value, list):
                new_params.extend([f"{key}={v}" for v in value])
            else:
                new_params.append(f"{key}={value}")
        new_parsed = parsed._replace(query='&'.join(new_params))
        new_url = urlunparse(new_parsed)
        resp = flask.redirect(new_url, 302)
        resp.set_cookie('sleepy-theme', theme, samesite='Lax')
        return resp
    elif flask.request.cookies.get('sleepy-theme'):
        flask.g.theme = flask.request.cookies.get('sleepy-theme')
    else:
        flask.g.theme = c.page.theme
    flask.g.secret = c.main.secret
    # Auth check
    path = flask.request.path
    secret_ok = False
    if flask.request.args.get('secret') == c.main.secret:
        secret_ok = True
    elif flask.request.headers.get('Sleepy-Secret') == c.main.secret:
        secret_ok = True
    elif flask.request.headers.get('Authorization', '').startswith('Bearer ') and flask.request.headers.get('Authorization', '')[7:] == c.main.secret:
        secret_ok = True
    elif flask.request.cookies.get('sleepy-secret') == c.main.secret:
        secret_ok = True
    elif flask.request.method == 'POST' and flask.request.is_json:
        body = flask.request.get_json(silent=True)
        if body and body.get('secret') == c.main.secret:
            secret_ok = True
    if path == '/api/device/screenshot' or path == '/api/device/screenshot/take' or path == '/api/device/screenshot/request' or path == '/api/device/screenshot/trigger' or path.startswith('/api/device/screenshot/') or path.startswith('/api/status/query') or path.startswith('/api/status/list') or path.startswith('/api/status/events'):
        # Public screenshot routes and read-only status routes
        pass
    elif path.startswith('/api/device/') or path.startswith('/api/status/set') or path.startswith('/panel/auth') or path.startswith('/panel/verify'):
        if not secret_ok:
            raise u.APIUnsuccessful(401, 'Wrong Secret')
    elif path.startswith('/panel/') and path != '/panel/login':
        if not secret_ok:
            return flask.redirect('/panel/login', 302)
    evt = p.trigger_event(pl.BeforeRequestHook())
    if evt and evt.interception:
        return evt.interception

@app.after_request
def after_request(resp):
    if init_error:
        return resp
    path = flask.request.path
    if c.metrics.enabled:
        d.record_metrics(path)
    l.info(f'[Request] {flask.g.ipstr} | {path} -> {resp.status_code} ({flask.g.perf()}ms)')
    evt = p.trigger_event(pl.AfterRequestHook(resp))
    if evt.interception:
        evt.response = flask.Response(evt.interception[0], evt.interception[1])
    evt.response.headers.add('X-Powered-By', 'Sleepy-Project')
    evt.response.headers.add('Sleepy-Version', f'{version_str}')
    return evt.response

# ========== Routes ==========

@app.route('/')
def index():
    more_text = c.page.more_text
    if c.metrics.enabled:
        daily, weekly, monthly, yearly, total = d.metric_data_index
        more_text = more_text.format(visit_daily=daily, visit_weekly=weekly, visit_monthly=monthly, visit_yearly=yearly, visit_total=total)
    main_card = render_template('main.index.html', _dirname='cards', username=c.page.name, status=d.status_dict[1], last_updated=datetime.fromtimestamp(d.last_updated, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') + ' (UTC+8)')
    more_info_card = render_template('more_info.index.html', _dirname='cards', more_text=more_text, username=c.page.name, learn_more_link=c.page.learn_more_link, learn_more_text=c.page.learn_more_text, available_themes=u.themes_available())
    cards = {'main': main_card, 'more-info': more_info_card}
    for name, values in p.index_cards.items():
        value = ''
        for v in values:
            value += (f'{v()}<br/>\n' if hasattr(v, '__call__') else f'{v}<br/>\n')
        cards[name] = value
    injects = [str(i()) if hasattr(i, '__call__') else str(i) for i in p.index_injects]
    evt = p.trigger_event(pl.IndexAccessEvent(page_title=c.page.title, page_desc=c.page.desc, page_favicon=c.page.favicon, page_background=c.page.background, cards=cards, injects=injects))
    if evt.interception:
        return evt.interception
    return render_template('index.html', page_title=evt.page_title, page_desc=evt.page_desc, page_favicon=evt.page_favicon, page_background=evt.page_background, cards=evt.cards, inject='\n'.join(evt.injects)) or flask.abort(404)

@app.route('/favicon.ico')
def favicon():
    evt = p.trigger_event(pl.FaviconAccessEvent(c.page.favicon))
    if evt.interception:
        return evt.interception
    return serve_public('favicon.ico') if evt.favicon_url == '/favicon.ico' else flask.redirect(evt.favicon_url, 302)

@app.route('/github')
def git_hub():
    return flask.redirect('https://github.com/sleepy-project/sleepy', 301)

@app.route('/none')
def none():
    return '', 204

@app.route('/api/meta')
def metadata():
    meta = {'success': True, 'version': version, 'version_str': version_str, 'timezone': c.main.timezone, 'page': {'name': c.page.name, 'title': c.page.title, 'desc': c.page.desc, 'favicon': c.page.favicon, 'background': c.page.background, 'theme': c.page.theme}, 'status': {'device_slice': c.status.device_slice, 'refresh_interval': c.status.refresh_interval, 'not_using': c.status.not_using, 'sorted': c.status.sorted, 'using_first': c.status.using_first}, 'metrics': c.metrics.enabled}
    evt = p.trigger_event(pl.MetadataAccessEvent(meta))
    return evt.interception if evt.interception else evt.metadata

@app.route('/api/metrics')
def metrics_route():
    evt = p.trigger_event(pl.MetricsAccessEvent(d.metrics_resp))
    return evt.interception if evt.interception else evt.metrics_response

def query():
    st = d.status_id
    try:
        stinfo = c.status.status_list[st].model_dump()
    except Exception:
        stinfo = {'id': -1, 'name': '[未知]', 'desc': f'未知的标识符 {st}', 'color': 'error'}
    ret = {'success': True, 'time': datetime.now().timestamp(), 'status': stinfo, 'device': d.device_list, 'last_updated': d.last_updated}
    if u.tobool(flask.request.args.get('meta', False)):
        ret['meta'] = metadata()
    if u.tobool(flask.request.args.get('metrics', False)):
        ret['metrics'] = d.metrics_resp
    evt = p.trigger_event(pl.QueryAccessEvent(ret))
    return evt.query_response

@app.route('/api/status/query')
def query_route():
    return query()

def _event_stream(event_id, ipstr):
    last_updated = None
    last_heartbeat = time.time()
    while True:
        current_time = time.time()
        current_updated = d.last_updated
        if last_updated != current_updated:
            last_updated = current_updated
            last_heartbeat = current_time
            event_id += 1
            yield f'id: {event_id}\nevent: update\ndata: {json.dumps(query(), ensure_ascii=False)}\n\n'
        elif current_time - last_heartbeat >= 30:
            event_id += 1
            yield f'id: {event_id}\nevent: heartbeat\ndata:\n\n'
            last_heartbeat = current_time
        time.sleep(1)

@app.route('/api/status/events')
def events():
    try:
        last_event_id = int(flask.request.headers.get('Last-Event-ID', '0'))
    except ValueError:
        raise u.APIUnsuccessful(400, 'Invalid Last-Event-ID')
    evt = p.trigger_event(pl.StreamConnectedEvent(last_event_id))
    if evt.interception:
        return evt.interception
    response = flask.Response(_event_stream(last_event_id, flask.g.ipstr), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.call_on_close(lambda: p.trigger_event(pl.StreamDisconnectedEvent()))
    return response

@app.route('/api/status/set')
def set_status():
    status = escape(flask.request.args.get('status'))
    try:
        status = int(status)
    except Exception:
        raise u.APIUnsuccessful(400, 'status must be int')
    if status != d.status_id:
        old_status = d.status
        new_status = d.get_status(status)
        evt = p.trigger_event(pl.StatusUpdatedEvent(old_exists=old_status[0], old_status=old_status[1], new_exists=new_status[0], new_status=new_status[1]))
        if evt.interception:
            return evt.interception
        d.status_id = evt.new_status.id
    return {'success': True, 'set_to': status}

@app.route('/api/status/list')
def get_status_list():
    evt = p.trigger_event(pl.StatuslistAccessEvent(c.status.status_list))
    return evt.interception if evt.interception else {'success': True, 'status_list': [i.model_dump() for i in evt.status_list]}

@app.route('/api/device/set', methods=['GET', 'POST'])
def device_set():
    if flask.request.method == 'GET':
        args = dict(flask.request.args)
        args.pop('secret', None)
        evt = p.trigger_event(pl.DeviceSetEvent(device_id=args.pop('id', None), show_name=args.pop('show_name', None), using=u.tobool(args.pop('using', None)), status=args.pop('status', None) or args.pop('app_name', None), fields=args))
        if evt.interception:
            return evt.interception
        d.device_set(id=evt.device_id, show_name=evt.show_name, using=evt.using, status=evt.status, fields=evt.fields)
    elif flask.request.method == 'POST':
        try:
            req = flask.request.get_json()
            evt = p.trigger_event(pl.DeviceSetEvent(device_id=req.get('id'), show_name=req.get('show_name'), using=req.get('using'), status=req.get('status') or req.get('app_name'), fields=req.get('fields') or {}))
            if evt.interception:
                return evt.interception
            d.device_set(id=evt.device_id, show_name=evt.show_name, using=evt.using, status=evt.status, fields=evt.fields)
        except Exception as e:
            if isinstance(e, u.APIUnsuccessful):
                raise
            raise u.APIUnsuccessful(400, f'missing param: {e}')
    else:
        raise u.APIUnsuccessful(405, 'Only GET/POST')
    return {'success': True}

@app.route('/api/device/remove')
def device_remove():
    device_id = flask.request.args.get('id')
    if not device_id:
        raise u.APIUnsuccessful(400, 'Missing device id')
    device = d.device_get(device_id)
    evt = p.trigger_event(pl.DeviceRemovedEvent(exists=bool(device), device_id=device_id, show_name=device.show_name if device else None, using=device.using if device else None, status=device.status if device else None, fields=device.fields if device else None))
    if evt.interception:
        return evt.interception
    d.device_remove(evt.device_id)
    return {'success': True}

@app.route('/api/device/clear')
def device_clear():
    evt = p.trigger_event(pl.DeviceClearedEvent(d._raw_device_list))
    if evt.interception:
        return evt.interception
    d.device_clear()
    return {'success': True}

_screenshot_requested = False
_screenshot_lock = __import__('threading').Lock()

@app.route('/api/device/screenshot', methods=['GET', 'POST'])
def device_screenshot():
    if flask.request.method == 'POST':
        # Upload screenshot from client
        global _screenshot_requested
        device_id = flask.request.form.get('device_id')
        if not device_id:
            raise u.APIUnsuccessful(400, 'Missing device_id')
        if 'screenshot' not in flask.request.files:
            raise u.APIUnsuccessful(400, 'Missing screenshot file')
        file = flask.request.files['screenshot']
        if file.filename == '':
            raise u.APIUnsuccessful(400, 'Empty filename')
        
        # Check if Vercel Blob is configured
        use_blob = os.environ.get('BLOB_READ_WRITE_TOKEN') is not None
        
        if use_blob:
            # Upload to Vercel Blob
            try:
                import vercel_blob
                screenshot_data = file.read()
                result = vercel_blob.put(path=f'screenshots/{device_id}.png', data=screenshot_data, options={'allowOverwrite': True})
                # Save the blob URL (not just the path) for serving
                blob_url = result.get('url', '')
                d.device_set(device_id, '', '', '', fields={'screenshot': blob_url, 'screenshot_requested': False})
            except Exception as e:
                l.error(f'Failed to upload screenshot to Blob: {e}')
                raise u.APIUnsuccessful(500, f'Failed to upload screenshot: {e}')
        else:
            # Fallback to local filesystem
            import os
            screenshot_dir = u.get_path('data/screenshots')
            os.makedirs(screenshot_dir, exist_ok=True)
            filename = f'{device_id}.png'
            file.save(os.path.join(screenshot_dir, filename))
            # Clear screenshot_requested flag and save screenshot path
            fields = {'screenshot': filename, 'screenshot_requested': False}
            d.device_set(device_id, '', '', '', fields=fields)
        
        return {'success': True, 'screenshot': f'screenshots/{device_id}.png'}
    else:
        # Serve latest screenshot
        if d is None:
            raise u.APIUnsuccessful(503, 'Not initialized')
        
        # Check if Vercel Blob is configured
        use_blob = os.environ.get('BLOB_READ_WRITE_TOKEN') is not None
        
        if use_blob:
            # Serve screenshot from Blob URL stored in device fields
            try:
                device = d.device_get('my-pc')
                screenshot_url = device.get('fields', {}).get('screenshot') if device else None
                
                if not screenshot_url:
                    raise u.APIUnsuccessful(404, 'No screenshots available')
                
                # Redirect directly to the blob URL
                return flask.redirect(screenshot_url, 302)
            except u.APIUnsuccessful:
                raise
            except Exception as e:
                l.error(f'Failed to get screenshot from Blob: {e}')
                raise u.APIUnsuccessful(500, f'Failed to get screenshot: {e}')
        else:
            # Fallback to local filesystem
            import os
            screenshot_dir = u.get_path('data/screenshots')
            if not os.path.exists(screenshot_dir):
                raise u.APIUnsuccessful(404, 'No screenshots')
            screenshots = [f for f in os.listdir(screenshot_dir) if f.endswith('.png')]
            if not screenshots:
                raise u.APIUnsuccessful(404, 'No screenshots available')
            return flask.send_file(os.path.join(screenshot_dir, screenshots[0]), mimetype='image/png')

@app.route('/api/device/screenshot/request', methods=['GET'])
def request_screenshot():
    """Check if a screenshot was requested (for client polling)"""
    try:
        # Check device fields for screenshot_requested flag
        device = d.device_get('my-pc')
        if device and device.get('fields', {}).get('screenshot_requested'):
            return {'requested': True}
        return {'requested': False}
    except Exception as e:
        l.debug(f'Check screenshot request error: {e}')
        return {'requested': False}

@app.route('/api/device/screenshot/trigger', methods=['POST'])
def trigger_screenshot():
    """Trigger a screenshot request (called by visitors)"""
    try:
        # Set screenshot_requested flag on device
        d.device_set(id='my-pc', show_name=None, using=None, status=None, 
                     fields={'screenshot_requested': True})
    except Exception as e:
        l.error(f'Failed to trigger screenshot: {e}')
        return {'success': False, 'error': str(e)}, 500
    return {'success': True, 'msg': 'Screenshot requested'}

@app.route('/api/device/screenshot/<filename>')
def get_screenshot(filename):
    import os
    screenshot_path = u.get_path(f'data/screenshots/{filename}')
    if not os.path.exists(screenshot_path):
        raise u.APIUnsuccessful(404, 'Screenshot not found')
    return flask.send_file(screenshot_path, mimetype='image/png')

@app.route('/api/device/private')
def device_private_mode():
    private = u.tobool(flask.request.args.get('private'))
    if private is None:
        raise u.APIUnsuccessful(400, 'private must be boolean')
    if private != d.private_mode:
        evt = p.trigger_event(pl.PrivateModeChangedEvent(d.private_mode, private))
        if evt.interception:
            return evt.interception
        d.private_mode = evt.new_status
    return {'success': True}

@app.route('/panel')
def admin_panel():
    cards = {}
    for name, card in p.panel_cards.items():
        cards[name] = card.copy()
        if hasattr(card['content'], '__call__'):
            cards[name]['content'] = card['content']()
    inject = ''.join(str(i()) + '\n' if hasattr(i, '__call__') else str(i) + '\n' for i in p.panel_injects)
    return render_template('panel.html', c=c, current_theme=flask.g.theme, available_themes=u.themes_available(), cards=cards, inject=inject) or flask.abort(404)

@app.route('/panel/login')
def login():
    if flask.request.cookies.get('sleepy-secret') == c.main.secret:
        return flask.redirect('/panel')
    return render_template('login.html', c=c, current_theme=flask.g.theme) or flask.abort(404)

@app.route('/panel/auth', methods=['POST'])
def auth():
    response = flask.make_response({'success': True, 'code': 'OK', 'message': 'Login successful'})
    response.set_cookie('sleepy-secret', c.main.secret, max_age=30*24*60*60, httponly=True, samesite='Lax')
    return response

@app.route('/panel/logout')
def logout():
    response = flask.make_response(flask.redirect('/panel/login'))
    response.delete_cookie('sleepy-secret')
    return response

@app.route('/panel/verify', methods=['GET', 'POST'])
def verify_secret():
    return {'success': True, 'code': 'OK', 'message': 'Secret verified'}

@app.route('/<path:path_name>')
def serve_public(path_name):
    if d is None:
        return flask.abort(404)
    file = d.get_cached_file('data/public', path_name) or d.get_cached_file('public', path_name)
    if file:
        return flask.send_file(file, mimetype=guess_type(path_name)[0] or 'text/plain')
    return flask.abort(404)

# ========== App Started ==========

# Run init immediately for local development
_do_init()

try:
    p.trigger_event(pl.AppStartedEvent())
except Exception:
    pass

if __name__ == '__main__':
    if c is not None:
        app.run(host=c.main.host, port=c.main.port, debug=c.main.debug, use_reloader=False, threaded=True)
    else:
        print(f'Failed to initialize: {init_error}')
