# coding: utf-8

from datetime import datetime
from logging import getLogger
from threading import Thread
from time import sleep, time
from typing import Any
from io import BytesIO
import json
import os

from werkzeug.security import safe_join
from flask import Flask
from objtyping import to_primitive
import pytz
import schedule

import utils as u
from models import ConfigModel, _StatusItemModel

try:
    import vercel_blob
except ImportError:
    vercel_blob = None

l = getLogger(__name__)

_DeviceStatusData = dict[str, str | int | float | bool | None | dict]

# -----


class Data:
    '''
    data 类, 使用 Vercel Blob 存储数据
    '''

    def __init__(self, config: ConfigModel, app: Flask):
        perf = u.perf_counter()
        self._app = app
        self._c = config
        self._use_blob = os.environ.get('BLOB_READ_WRITE_TOKEN') is not None

        if self._use_blob:
            l.info('[data] 使用 Vercel Blob 存储')
        else:
            l.info('[data] 未配置 Vercel Blob，使用内存存储')
            self._memory_store = {
                'main': {
                    'status': 0,
                    'private_mode': False,
                    'last_updated': time()
                },
                'devices': {},
                'metrics_meta': {
                    'today': '',
                    'week': '',
                    'month': '',
                    'year': ''
                },
                'metrics': {},
                'plugin': {}
            }

        # 初始化数据
        self._init_data()

        # 启动 schedule loop
        self._schedule_loop_th = Thread(target=self._schedule_loop, daemon=True)
        self._schedule_loop_th.start()

        l.debug(f'[data] init took {perf()}ms')

    def _init_data(self):
        '''初始化数据'''
        if self._use_blob:
            self._ensure_main_data()
            if self._c.metrics.enabled:
                self._ensure_metrics_meta()

    def _ensure_main_data(self):
        '''确保主数据存在'''
        try:
            main_data = self._blob_get_json('main.json')
            if main_data is None:
                l.debug(f'[data] main_data not exist, creating a new one')
                main_data = {
                    'status': 0,
                    'private_mode': False,
                    'last_updated': time()
                }
                self._blob_put_json('main.json', main_data)
        except Exception as e:
            l.error(f'[_ensure_main_data] Error: {e}')

    def _ensure_metrics_meta(self):
        '''确保 metrics 元数据存在'''
        try:
            meta = self._blob_get_json('metrics_meta.json')
            if meta is None:
                l.debug(f'[data] metrics_metadata not exist, creating a new one')
                meta = {
                    'today': '',
                    'week': '',
                    'month': '',
                    'year': ''
                }
                self._blob_put_json('metrics_meta.json', meta)
        except Exception as e:
            l.error(f'[_ensure_metrics_meta] Error: {e}')

    # --- Vercel Blob 辅助方法 ---

    def _blob_get_json(self, path: str) -> dict | None:
        '''从 Blob 获取 JSON 数据'''
        if not self._use_blob:
            return None
        try:
            # 使用 head() 获取元数据
            meta = vercel_blob.head(path)
            if meta and 'downloadUrl' in meta:
                url = meta['downloadUrl']
                # 下载文件内容
                import requests
                resp = requests.get(url)
                if resp.status_code == 200:
                    return json.loads(resp.text)
            return None
        except Exception:
            # Blob 不存在或任何错误时返回 None
            return None

    def _blob_put_json(self, path: str, data: dict):
        '''将 JSON 数据存储到 Blob'''
        if not self._use_blob:
            return
        try:
            content = json.dumps(data, ensure_ascii=False).encode('utf-8')
            vercel_blob.put(path=path, data=content)
        except Exception as e:
            l.error(f'[_blob_put_json] Error saving {path}: {e}')
            raise

    def _blob_get_bytes(self, path: str) -> bytes | None:
        '''从 Blob 获取原始字节数据'''
        if not self._use_blob:
            return None
        try:
            meta = vercel_blob.head(path)
            if meta and 'downloadUrl' in meta:
                url = meta['downloadUrl']
                import requests
                resp = requests.get(url)
                if resp.status_code == 200:
                    return resp.content
            return None
        except Exception as e:
            l.debug(f'[_blob_get_bytes] {path} not found or error: {e}')
            return None

    def _blob_put_bytes(self, path: str, data: bytes):
        '''将字节数据存储到 Blob'''
        if not self._use_blob:
            return
        try:
            vercel_blob.put(path=path, data=data)
        except Exception as e:
            l.error(f'[_blob_put_bytes] Error saving {path}: {e}')
            raise

    def _blob_delete(self, path: str):
        '''从 Blob 删除文件'''
        if not self._use_blob:
            return
        try:
            vercel_blob.delete(blob_urls=path)
        except Exception as e:
            l.debug(f'[_blob_delete] {path} not found or error: {e}')

    def _blob_list(self, prefix: str = '') -> list[str]:
        '''列出 Blob 中指定前缀的文件'''
        if not self._use_blob:
            return []
        try:
            result = vercel_blob.list()
            if result and 'blobs' in result:
                if prefix:
                    return [b['pathname'] for b in result['blobs'] if b['pathname'].startswith(prefix)]
                return [b['pathname'] for b in result['blobs']]
            return []
        except Exception as e:
            l.error(f'[_blob_list] Error: {e}')
            return []

    # --- 内存存储辅助方法 ---

    def _mem_get(self, *keys) -> Any:
        '''从内存存储获取数据'''
        data = self._memory_store
        for key in keys:
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                return None
        return data

    def _mem_set(self, keys: list, value: Any):
        '''设置内存存储数据'''
        data = self._memory_store
        for key in keys[:-1]:
            if key not in data:
                data[key] = {}
            data = data[key]
        data[keys[-1]] = value

    # --- Schedule Loop ---

    def _schedule_loop(self):
        if self._c.metrics.enabled:
            self._metrics_refresh()
            schedule.every().day.at('00:00:00', self._c.main.timezone).do(self._metrics_refresh)
        schedule.every(self._c.main.cache_age).seconds.do(self._clean_cache)

        while True:
            schedule.run_pending()
            sleep(1)

    # --- 主程序数据访问 ---

    @property
    def status_id(self) -> int:
        '''当前的状态 id'''
        if self._use_blob:
            main_data = self._blob_get_json('main.json')
            return main_data.get('status', 0) if main_data else 0
        else:
            return self._mem_get('main', 'status') or 0

    @status_id.setter
    def status_id(self, value: int):
        if self._use_blob:
            main_data = self._blob_get_json('main.json')
            if main_data:
                main_data['status'] = value
                self._blob_put_json('main.json', main_data)
        else:
            self._mem_set(['main', 'status'], value)

    def get_status(self, status_id: int) -> tuple[bool, _StatusItemModel]:
        '''用 id 获取状态'''
        try:
            return True, self._c.status.status_list[status_id]
        except IndexError:
            return False, _StatusItemModel(
                id=self.status_id,
                name='Unknown',
                desc='未知的标识符，可能是配置问题。',
                color='error'
            )

    @property
    def status(self) -> tuple[bool, _StatusItemModel]:
        '''获取当前状态'''
        return self.get_status(self.status_id)

    @property
    def status_dict(self) -> tuple[bool, dict[str, int | str]]:
        '''获取当前状态'''
        status = self.status
        return status[0], to_primitive(self.status[1])

    @property
    def private_mode(self) -> bool:
        '''是否开启隐私模式'''
        if self._use_blob:
            main_data = self._blob_get_json('main.json')
            return main_data.get('private_mode', False) if main_data else False
        else:
            return self._mem_get('main', 'private_mode') or False

    @private_mode.setter
    def private_mode(self, value: bool):
        if self._use_blob:
            main_data = self._blob_get_json('main.json')
            if main_data:
                main_data['private_mode'] = value
                self._blob_put_json('main.json', main_data)
        else:
            self._mem_set(['main', 'private_mode'], value)

    @property
    def last_updated(self) -> float:
        '''数据最后更新时间 (utc)'''
        if self._use_blob:
            main_data = self._blob_get_json('main.json')
            return main_data.get('last_updated', time()) if main_data else time()
        else:
            return self._mem_get('main', 'last_updated') or time()

    @last_updated.setter
    def last_updated(self, value: float):
        if self._use_blob:
            main_data = self._blob_get_json('main.json')
            if main_data:
                main_data['last_updated'] = value
                self._blob_put_json('main.json', main_data)
        else:
            self._mem_set(['main', 'last_updated'], value)

    # --- 设备状态接口 ---

    def _get_all_devices(self) -> dict:
        '''获取所有设备数据'''
        if self._use_blob:
            devices = {}
            device_paths = self._blob_list(prefix='devices/')
            for path in device_paths:
                device_id = path.replace('devices/', '').replace('.json', '')
                device_data = self._blob_get_json(path)
                if device_data:
                    devices[device_id] = device_data
            return devices
        else:
            return self._mem_get('devices') or {}

    @property
    def _raw_device_list_dict(self) -> dict[str, dict[str, str | int | float | bool]]:
        '''原始设备列表'''
        if self.private_mode:
            return {}
        return self._get_all_devices()

    @property
    def device_list(self) -> dict[str, dict[str, Any]]:
        '''排序后设备列表'''
        if self.private_mode:
            return {}

        devicelst = self._raw_device_list_dict

        if self._c.status.using_first:
            using_devices = {}
            not_using_devices = {}
            unknown_devices = {}

            for k, v in devicelst.items():
                if v.get('using') == True:
                    using_devices[k] = v
                elif v.get('using') == False:
                    if self._c.status.not_using:
                        v['status'] = self._c.status.not_using
                    not_using_devices[k] = v
                else:
                    unknown_devices[k] = v

            if self._c.status.sorted:
                using_devices = dict(sorted(using_devices.items()))
                not_using_devices = dict(sorted(not_using_devices.items()))
                unknown_devices = dict(sorted(unknown_devices.items()))

            devicelst = {}
            devicelst.update(using_devices)
            devicelst.update(not_using_devices)
            devicelst.update(unknown_devices)
        else:
            if self._c.status.not_using:
                for d in devicelst.keys():
                    if devicelst[d].get('using') == False:
                        devicelst[d]['status'] = self._c.status.not_using
            if self._c.status.sorted:
                devicelst = dict(sorted(devicelst.items()))

        return devicelst

    def device_get(self, id: str) -> dict | None:
        '''获取指定设备状态'''
        if self._use_blob:
            return self._blob_get_json(f'devices/{id}.json')
        else:
            devices = self._mem_get('devices') or {}
            return devices.get(id)

    def device_set(self, id: str | None = None,
                   show_name: str | None = None,
                   using: bool | None = None,
                   status: str | None = None,
                   fields: dict = {}):
        '''设备状态设置'''
        if not id:
            raise u.APIUnsuccessful(400, 'device id cannot be empty!')

        if self._use_blob:
            device = self._blob_get_json(f'devices/{id}.json')
            if not device:
                if not show_name:
                    raise u.APIUnsuccessful(400, 'device show_name cannot be empty!')
                device = {
                    'id': id,
                    'show_name': show_name,
                    'using': None,
                    'status': None,
                    'fields': {},
                    'last_updated': time()
                }

            device['show_name'] = show_name or device.get('show_name', '')
            if using is not None:
                device['using'] = using
            if status:
                device['status'] = status
            device['fields'] = u.deep_merge_dict(device.get('fields', {}), fields)
            device['last_updated'] = time()

            self._blob_put_json(f'devices/{id}.json', device)
        else:
            devices = self._mem_get('devices') or {}
            if id not in devices:
                if not show_name:
                    raise u.APIUnsuccessful(400, 'device show_name cannot be empty!')
                devices[id] = {
                    'id': id,
                    'show_name': show_name,
                    'using': None,
                    'status': None,
                    'fields': {},
                    'last_updated': time()
                }

            device = devices[id]
            device['show_name'] = show_name or device.get('show_name', '')
            if using is not None:
                device['using'] = using
            if status:
                device['status'] = status
            device['fields'] = u.deep_merge_dict(device.get('fields', {}), fields)
            device['last_updated'] = time()

            self._mem_set(['devices'], devices)

        self.last_updated = time()

    def device_remove(self, id: str):
        '''移除单个设备'''
        if self._use_blob:
            self._blob_delete(f'devices/{id}.json')
        else:
            devices = self._mem_get('devices') or {}
            if id in devices:
                del devices[id]
                self._mem_set(['devices'], devices)
        self.last_updated = time()

    def device_clear(self):
        '''清除设备状态'''
        if self._use_blob:
            device_paths = self._blob_list(prefix='devices/')
            for path in device_paths:
                self._blob_delete(path)
        else:
            self._mem_set(['devices'], {})
        self.last_updated = time()

    # --- 统计数据访问 ---

    def record_metrics(self, path: str, count: int = 1, override: bool = False):
        '''记录 metrics 数据'''
        if not path in self._c.metrics.allow_list:
            return

        if self._use_blob:
            metric = self._blob_get_json(f'metrics/{path}.json')
            if not metric:
                metric = {
                    'path': path,
                    'daily': 0,
                    'weekly': 0,
                    'monthly': 0,
                    'yearly': 0,
                    'total': 0
                }

            if override:
                metric['daily'] = count
                metric['weekly'] = count
                metric['monthly'] = count
                metric['yearly'] = count
                metric['total'] = count
            else:
                metric['daily'] += count
                metric['weekly'] += count
                metric['monthly'] += count
                metric['yearly'] += count
                metric['total'] += count

            self._blob_put_json(f'metrics/{path}.json', metric)
        else:
            metrics = self._mem_get('metrics') or {}
            if path not in metrics:
                metrics[path] = {
                    'path': path,
                    'daily': 0,
                    'weekly': 0,
                    'monthly': 0,
                    'yearly': 0,
                    'total': 0
                }

            metric = metrics[path]
            if override:
                metric['daily'] = count
                metric['weekly'] = count
                metric['monthly'] = count
                metric['yearly'] = count
                metric['total'] = count
            else:
                metric['daily'] += count
                metric['weekly'] += count
                metric['monthly'] += count
                metric['yearly'] += count
                metric['total'] += count

            self._mem_set(['metrics'], metrics)

    @property
    def metrics_data(self) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
        '''获取 metrics 数据'''
        if self._use_blob:
            metric_paths = self._blob_list(prefix='metrics/')
            daily = {}
            weekly = {}
            monthly = {}
            yearly = {}
            total = {}

            for path in metric_paths:
                metric = self._blob_get_json(path)
                if metric:
                    p = metric.get('path', '')
                    daily[p] = metric.get('daily', 0)
                    weekly[p] = metric.get('weekly', 0)
                    monthly[p] = metric.get('monthly', 0)
                    yearly[p] = metric.get('yearly', 0)
                    total[p] = metric.get('total', 0)

            return (daily, weekly, monthly, yearly, total)
        else:
            metrics = self._mem_get('metrics') or {}
            daily = {}
            weekly = {}
            monthly = {}
            yearly = {}
            total = {}

            for p, m in metrics.items():
                daily[p] = m.get('daily', 0)
                weekly[p] = m.get('weekly', 0)
                monthly[p] = m.get('monthly', 0)
                yearly[p] = m.get('yearly', 0)
                total[p] = m.get('total', 0)

            return (daily, weekly, monthly, yearly, total)

    @property
    def metric_data_index(self) -> tuple[int, int, int, int, int]:
        '''获取主页 (/) 的 metric 数据'''
        if self._use_blob:
            metric = self._blob_get_json('metrics/.json')
            if metric:
                return (
                    metric.get('daily', 0),
                    metric.get('weekly', 0),
                    metric.get('monthly', 0),
                    metric.get('yearly', 0),
                    metric.get('total', 0)
                )
            return (0, 0, 0, 0, 0)
        else:
            metrics = self._mem_get('metrics') or {}
            metric = metrics.get('/')
            if metric:
                return (
                    metric.get('daily', 0),
                    metric.get('weekly', 0),
                    metric.get('monthly', 0),
                    metric.get('yearly', 0),
                    metric.get('total', 0)
                )
            return (0, 0, 0, 0, 0)

    @property
    def metrics_resp(self) -> dict[str, Any]:
        '''获取 metrics 返回'''
        enabled = self._c.metrics.enabled
        if enabled:
            daily, weekly, monthly, yearly, total = self.metrics_data
            now = datetime.now(pytz.timezone(self._c.main.timezone))
            return {
                'success': True,
                'enabled': True,
                'time': now.timestamp(),
                'time_local': now.strftime('%Y-%m-%d %H:%M:%S'),
                'timezone': self._c.main.timezone,
                'daily': daily,
                'weekly': weekly,
                'monthly': monthly,
                'yearly': yearly,
                'total': total
            }
        else:
            return {
                'success': True,
                'enabled': False
            }

    def _metrics_refresh(self):
        '''刷新 metrics 数据'''
        perf = u.perf_counter()
        try:
            if self._use_blob:
                meta_metrics = self._blob_get_json('metrics_meta.json')
                if not meta_metrics:
                    meta_metrics = {
                        'today': '',
                        'week': '',
                        'month': '',
                        'year': ''
                    }
            else:
                meta_metrics = self._mem_get('metrics_meta')
                if not meta_metrics:
                    meta_metrics = {
                        'today': '',
                        'week': '',
                        'month': '',
                        'year': ''
                    }

            now = datetime.now(pytz.timezone(self._c.main.timezone))
            year = f'{now.year}'
            month = f'{now.year}-{now.month}'
            today = f'{now.year}-{now.month}-{now.day}'
            week = f'{now.year}-{now.isocalendar().week}'

            if today != meta_metrics.get('today', ''):
                l.debug(f'[metrics] today changed: {meta_metrics.get("today", "")} -> {today}')
                meta_metrics['today'] = today
                self._reset_metric_field('daily')

            if week != meta_metrics.get('week', ''):
                l.debug(f'[metrics] week changed: {meta_metrics.get("week", "")} -> {week}')
                meta_metrics['week'] = week
                self._reset_metric_field('weekly')

            if month != meta_metrics.get('month', ''):
                l.debug(f'[metrics] month changed: {meta_metrics.get("month", "")} -> {month}')
                meta_metrics['month'] = month
                self._reset_metric_field('monthly')

            if year != meta_metrics.get('year', ''):
                l.debug(f'[metrics] year changed: {meta_metrics.get("year", "")} -> {year}')
                meta_metrics['year'] = year
                self._reset_metric_field('yearly')

            if self._use_blob:
                self._blob_put_json('metrics_meta.json', meta_metrics)
            else:
                self._mem_set(['metrics_meta'], meta_metrics)

        except Exception as e:
            l.error(f'[_metrics_refresh] Error: {e}')
        l.debug(f'[_metrics_refresh] took {perf()}ms')

    def _reset_metric_field(self, field: str):
        '''重置所有 metrics 的指定字段'''
        if self._use_blob:
            metric_paths = self._blob_list(prefix='metrics/')
            for path in metric_paths:
                metric = self._blob_get_json(path)
                if metric:
                    metric[field] = 0
                    self._blob_put_json(path, metric)
        else:
            metrics = self._mem_get('metrics') or {}
            for path, metric in metrics.items():
                metric[field] = 0
            self._mem_set(['metrics'], metrics)

    # --- 插件数据访问 ---

    def get_plugin_data(self, id: str) -> dict:
        '''获取插件数据'''
        if self._use_blob:
            data = self._blob_get_json(f'plugin/{id}.json')
            if data is None:
                self._blob_put_json(f'plugin/{id}.json', {})
                return {}
            return data
        else:
            plugins = self._mem_get('plugin') or {}
            if id not in plugins:
                plugins[id] = {}
                self._mem_set(['plugin'], plugins)
            return plugins[id]

    def set_plugin_data(self, id: str, data: dict):
        '''设置插件数据'''
        if self._use_blob:
            self._blob_put_json(f'plugin/{id}.json', data)
        else:
            plugins = self._mem_get('plugin') or {}
            plugins[id] = data
            self._mem_set(['plugin'], plugins)

    # --- 缓存系统 ---

    _cache: dict[str, tuple[float, BytesIO]] = {}

    def get_cached_file(self, dirname: str, filename: str) -> BytesIO | None:
        '''加载文件 (经过缓存)'''
        filepath = safe_join(u.get_path(dirname), filename)
        if not filepath:
            return None
        try:
            if self._c.main.debug:
                with open(filepath, 'rb') as f:
                    return BytesIO(f.read())
            else:
                cache_key = f'f-{dirname}/{filename}'
                now = time()
                cached = self._cache.get(cache_key)
                if cached and now - cached[0] < self._c.main.cache_age:
                    return cached[1]
                else:
                    with open(filepath, 'rb') as f:
                        ret = BytesIO(f.read())
                    self._cache[cache_key] = (now, ret)
                    return ret
        except FileNotFoundError or IsADirectoryError:
            return None

    def get_cached_text(self, dirname: str, filename: str) -> str | None:
        '''加载文本文件 (经过缓存)'''
        raw = self.get_cached_file(dirname, filename)
        if raw:
            try:
                return str(raw.getvalue(), encoding='utf-8')
            except UnicodeDecodeError:
                return None
        else:
            return None

    def _clean_cache(self):
        '''清理过期缓存'''
        if self._c.main.debug:
            return
        now = time()
        for name in list(self._cache.keys()):
            if now - self._cache.get(name, (now, ''))[0] > self._c.main.cache_age:
                f = self._cache.pop(name, (0, None))[1]
                if f:
                    f.close()
