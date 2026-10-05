"""住まいコンパス — Streamlit版 v15（別プロセス検索 / 表示範囲DB読込 / SRC・築20年以内 / HOME'S・SUUMO）
実行: streamlit run app.py
依存: streamlit>=1.50,<2 / folium>=0.18,<1 / streamlit-folium>=0.24,<1 / requests>=2.32,<3
実物件の家賃メッシュ表示。通勤は概算。公開情報取得はサイトの仕様・接続状況に依存。
"""
from __future__ import annotations
import csv
import os
import sys
import subprocess
import base64
import hashlib
import heapq
import io
import math
import uuid
import json
import re
import statistics
import time
import concurrent.futures
import functools
import threading
import urllib.robotparser
import unicodedata
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from html import escape
from urllib.parse import urlencode, urlparse, urljoin, parse_qsl, urlunparse

import requests
import streamlit as st

STATIONS = [{'name': '東京', 'lat': 35.6812, 'lng': 139.7671, 'region': '東京'},
 {'name': '品川', 'lat': 35.6285, 'lng': 139.7388, 'region': '東京'},
 {'name': '大崎', 'lat': 35.6197, 'lng': 139.7286, 'region': '東京'},
 {'name': '五反田', 'lat': 35.6264, 'lng': 139.7235, 'region': '東京'},
 {'name': '目黒', 'lat': 35.6339, 'lng': 139.7158, 'region': '東京'},
 {'name': '恵比寿', 'lat': 35.6467, 'lng': 139.7101, 'region': '東京'},
 {'name': '渋谷', 'lat': 35.658, 'lng': 139.7016, 'region': '東京'},
 {'name': '新宿', 'lat': 35.6909, 'lng': 139.7003, 'region': '東京'},
 {'name': '池袋', 'lat': 35.7295, 'lng': 139.7109, 'region': '東京'},
 {'name': '上野', 'lat': 35.7138, 'lng': 139.777, 'region': '東京'},
 {'name': '秋葉原', 'lat': 35.6984, 'lng': 139.7731, 'region': '東京'},
 {'name': '赤羽', 'lat': 35.778, 'lng': 139.7209, 'region': '東京'},
 {'name': '川口', 'lat': 35.8019, 'lng': 139.7175, 'region': '埼玉'},
 {'name': '浦和', 'lat': 35.8585, 'lng': 139.6571, 'region': '埼玉'},
 {'name': '大宮', 'lat': 35.9063, 'lng': 139.6238, 'region': '埼玉'},
 {'name': '大井町', 'lat': 35.6063, 'lng': 139.7346, 'region': '東京'},
 {'name': '蒲田', 'lat': 35.5625, 'lng': 139.716, 'region': '東京'},
 {'name': '川崎', 'lat': 35.5313, 'lng': 139.697, 'region': '神奈川'},
 {'name': '横浜', 'lat': 35.4662, 'lng': 139.622, 'region': '神奈川'},
 {'name': '武蔵小杉', 'lat': 35.575, 'lng': 139.6595, 'region': '神奈川'},
 {'name': '日吉', 'lat': 35.553, 'lng': 139.6468, 'region': '神奈川'},
 {'name': '自由が丘', 'lat': 35.6074, 'lng': 139.6685, 'region': '東京'},
 {'name': '中目黒', 'lat': 35.6443, 'lng': 139.699, 'region': '東京'},
 {'name': '中野', 'lat': 35.7058, 'lng': 139.6658, 'region': '東京'},
 {'name': '荻窪', 'lat': 35.7045, 'lng': 139.6201, 'region': '東京'},
 {'name': '吉祥寺', 'lat': 35.7032, 'lng': 139.5797, 'region': '東京'},
 {'name': '三鷹', 'lat': 35.7027, 'lng': 139.5603, 'region': '東京'},
 {'name': '国分寺', 'lat': 35.7001, 'lng': 139.4808, 'region': '東京'},
 {'name': '立川', 'lat': 35.6982, 'lng': 139.4137, 'region': '東京'},
 {'name': '錦糸町', 'lat': 35.696, 'lng': 139.814, 'region': '東京'},
 {'name': '新小岩', 'lat': 35.7169, 'lng': 139.8586, 'region': '東京'},
 {'name': '市川', 'lat': 35.7289, 'lng': 139.9084, 'region': '千葉'},
 {'name': '船橋', 'lat': 35.7017, 'lng': 139.985, 'region': '千葉'},
 {'name': '津田沼', 'lat': 35.6907, 'lng': 140.0207, 'region': '千葉'},
 {'name': '千葉', 'lat': 35.6134, 'lng': 140.1133, 'region': '千葉'},
 {'name': '北千住', 'lat': 35.7494, 'lng': 139.805, 'region': '東京'},
 {'name': '松戸', 'lat': 35.7847, 'lng': 139.9007, 'region': '千葉'},
 {'name': '柏', 'lat': 35.8622, 'lng': 139.9711, 'region': '千葉'},
 {'name': '二子玉川', 'lat': 35.6117, 'lng': 139.6267, 'region': '東京'},
 {'name': '溝の口', 'lat': 35.5998, 'lng': 139.6115, 'region': '神奈川'},
 {'name': '南浦和', 'lat': 35.8476, 'lng': 139.669, 'region': '埼玉'}]
LINES = [{'name': '山手線',
  'seq': ['東京', '品川', '大崎', '五反田', '目黒', '恵比寿', '渋谷', '新宿', '池袋', '上野', '秋葉原', '東京'],
  'rate': 136,
  'section': '新大久保 → 新宿',
  'period': '7:44〜8:44'},
 {'name': '京浜東北線',
  'seq': ['大宮', '浦和', '南浦和', '川口', '赤羽', '上野', '秋葉原', '東京', '品川', '大井町', '蒲田', '川崎', '横浜'],
  'rate': 158,
  'section': '川口 → 赤羽',
  'period': '7:20〜8:20'},
 {'name': '埼京線',
  'seq': ['大宮', '赤羽', '池袋', '新宿', '渋谷', '恵比寿', '大崎'],
  'rate': 167,
  'section': '板橋 → 池袋',
  'period': '7:51〜8:51'},
 {'name': '中央線快速',
  'seq': ['東京', '新宿', '中野', '荻窪', '吉祥寺', '三鷹', '国分寺', '立川'],
  'rate': 158,
  'section': '中野 → 新宿',
  'period': '7:35〜8:35'},
 {'name': '東海道線', 'seq': ['東京', '品川', '川崎', '横浜'], 'rate': 159, 'section': '川崎 → 品川', 'period': '7:39〜8:39'},
 {'name': '横須賀線',
  'seq': ['東京', '品川', '武蔵小杉', '横浜'],
  'rate': 138,
  'section': '武蔵小杉 → 西大井',
  'period': '7:26〜8:26'},
 {'name': '東横線',
  'seq': ['渋谷', '中目黒', '自由が丘', '武蔵小杉', '日吉', '横浜'],
  'rate': 124,
  'section': '祐天寺 → 中目黒',
  'period': '7:50〜8:50'},
 {'name': '総武線快速',
  'seq': ['東京', '錦糸町', '新小岩', '市川', '船橋', '津田沼', '千葉'],
  'rate': 160,
  'section': '新小岩 → 錦糸町',
  'period': '7:31〜8:31'},
 {'name': '常磐線快速',
  'seq': ['上野', '北千住', '松戸', '柏'],
  'rate': 142,
  'section': '三河島 → 日暮里',
  'period': '7:26〜8:26'},
 {'name': '南武線',
  'seq': ['川崎', '武蔵小杉', '溝の口', '立川'],
  'rate': 156,
  'section': '武蔵中原 → 武蔵小杉',
  'period': '7:30〜8:30'},
 {'name': '田園都市線', 'seq': ['渋谷', '二子玉川', '溝の口'], 'rate': 138, 'section': '池尻大橋 → 渋谷', 'period': '7:50〜8:50'}]
BY_NAME = {s['name']: s for s in STATIONS}
LAYOUT_GROUPS = {
    '1K・1L・1DK': ('1K', '1L', '1DK'),
    '1LDK・2DK': ('1LDK', '2DK'),
    '2LDK・3DK': ('2LDK', '3DK'),
    '3LDK': ('3LDK',),
}
LAYOUTS = tuple(LAYOUT_GROUPS)
TARGET_LAYOUTS = tuple(dict.fromkeys(x for group in LAYOUT_GROUPS.values() for x in group))
SEARCHABLE_LAYOUTS = TARGET_LAYOUTS
# Keep legacy values readable so an old Supabase store never blocks startup.
STORAGE_LAYOUTS = set(TARGET_LAYOUTS) | {'1R','2K','3K','4K','4DK','4LDK'}
TARGET_STRUCTURE = 'SRC'
MAX_BUILDING_AGE = 20
CROWD_SOURCE = 'https://www.mlit.go.jp/report/press/content/002015142.pdf'
CSV_FIELDS = ['id', 'name', 'station', 'layout', 'rent', 'management', 'common', 'url']
FACILITY_FILTERS = {'スーパー': '[shop=supermarket]', 'コンビニ': '[shop=convenience]',
                    '公園': '[leisure=park]', '病院': '[amenity~"hospital|clinic"]'}


def distance(a, b):
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dlat, dlng = lat2-lat1, math.radians(b['lng']-a['lng'])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2
    return 6371*2*math.asin(math.sqrt(min(1, max(0, h))))


def build_graph():
    graph = {s['name']: [] for s in STATIONS}
    for i, line in enumerate(LINES):
        for a, b in zip(line['seq'], line['seq'][1:]):
            km = distance(BY_NAME[a], BY_NAME[b])*1.15
            graph[a].append((b, i, km, km/40*60))
            graph[b].append((a, i, km, km/40*60))
    return graph


GRAPH = build_graph()


@functools.lru_cache(maxsize=512)
def route(origin, destination):
    """Line-aware Dijkstra: 5 min for each line change, no timetable or through services."""
    if origin not in GRAPH or destination not in GRAPH:
        raise ValueError('登録されていない駅です。')
    queue = [(0.0, 0, origin, -1, 0.0, 0, (), (origin,))]
    visited, serial = set(), 0
    while queue:
        minutes, _, node, last_line, km, transfers, legs, path = heapq.heappop(queue)
        if (node, last_line) in visited:
            continue
        visited.add((node, last_line))
        if node == destination:
            return dict(time=minutes, km=km, transfers=transfers, legs=legs, path=path)
        for next_node, line, edge_km, edge_time in GRAPH[node]:
            change = int(last_line != -1 and last_line != line)
            serial += 1
            heapq.heappush(queue, (minutes+edge_time+5*change, serial, next_node, line,
                                   km+edge_km, transfers+change, legs+((node, next_node, line),),
                                   path+(next_node,)))
    raise ValueError('登録路線で到達できません。')


MD_CODES = {
    '1K':'11', '1DK':'12', '1LDK':'15',
    '2DK':'22', '2LDK':'25',
    '3DK':'32', '3LDK':'35',
}
DEFAULT_URL = 'https://www.homes.co.jp/chintai/tokyo/ikebukuro_00488-st/list/'
HEADERS = {'User-Agent': 'SumaiCompass/4.0 (personal rental map)'}
_HTTP = threading.local()

def http_session():
    if not hasattr(_HTTP, 'session'):
        _HTTP.session = requests.Session()
    return _HTTP.session

REAL_FIELDS = ['id', 'name', 'address', 'map_address', 'address_match', 'location_confidence',
               'lat', 'lng', 'layout', 'rent', 'fees', 'area', 'floor',
               'structure', 'building_age', 'built_year', 'coordinate_source',
               'url', 'fetched_at', 'modified', 'source']
COLORS = ['#2166ac', '#1d8fa7', '#48aa96', '#87b85c', '#d2be42', '#e9a248', '#df7245', '#c94049']
BANDS = [15, 20, 22.5, 25, 27.5, 30, 35]


def safe_source_url(url):
    try:
        p = urlparse(url)
        return (p.scheme == 'https' and p.hostname == 'www.homes.co.jp' and not p.username
                and not p.password and p.port in (None, 443) and p.path.startswith('/chintai/'))
    except (ValueError, TypeError):
        return False


def fetch_html(url):
    if not safe_source_url(url):
        raise ValueError('LIFULL HOME’Sの賃貸ページURLを指定してください。')
    with _HOMES_REQUEST_SEMAPHORE:
        response = http_session().get(url, headers=HEADERS, timeout=(20, 20))
    response.raise_for_status()
    if not safe_source_url(response.url):
        raise ValueError('物件ページ以外に移動しました。')
    if len(response.content) > 4_000_000:
        raise ValueError('ページが大きすぎます。')
    response.encoding = 'utf-8'
    return response.text


@functools.lru_cache(maxsize=1)
def check_robots():
    with _HOMES_REQUEST_SEMAPHORE:
        response = http_session().get('https://www.homes.co.jp/robots.txt', headers=HEADERS, timeout=(20, 20))
    response.raise_for_status()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(response.text.splitlines())
    return rp.can_fetch('SumaiCompass', DEFAULT_URL) and rp.can_fetch('SumaiCompass', 'https://www.homes.co.jp/chintai/room/test/')


def search_url(base, layout, page):
    p = urlparse(base)
    if not safe_source_url(base) or not p.path.endswith('/list/'):
        raise ValueError('駅や地域の「物件一覧」ページのURLを指定してください。')
    args = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith('cond[madori]') and k != 'page']
    args += [(f'cond[madori][{MD_CODES[layout]}]', MD_CODES[layout]), ('page', str(page))]
    return urlunparse(p._replace(query=urlencode(args), fragment=''))


def room_links(html):
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.select('a[href]'):
        url = urljoin('https://www.homes.co.jp', a['href']).split('?')[0]
        if safe_source_url(url) and re.fullmatch(r'/chintai/(room/[a-zA-Z0-9]+|b-[0-9]+)/', urlparse(url).path) and url not in links:
            links.append(url)
    # Room URLs and agency URLs can represent the same unit; canonical URL is used later.
    return links


def money(value):
    if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0:
        return int(value)
    text = str(value).replace(',', '').replace(' ', '').strip()
    if text in ('無', 'なし', '-', '0', '0円'):
        return 0
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(万円|円)?(?:/月)?', text)
    if not match:
        raise ValueError('家賃または管理費等が読み取れません。')
    return round(float(match[1])*(10000 if match[2] == '万円' else 1))


def json_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from json_nodes(item)
    elif isinstance(value, dict):
        yield value
        if '@graph' in value:
            yield from json_nodes(value['@graph'])


def normalize_structure(value):
    value = unicodedata.normalize('NFKC', str(value or '')).upper().replace(' ', '')
    if '鉄骨鉄筋' in value or re.search(r'(^|[^A-Z])SRC([^A-Z]|$)', value):
        return 'SRC'
    if '鉄筋コンクリート' in value or re.search(r'(^|[^A-Z])RC([^A-Z]|$)', value):
        return 'RC'
    if '軽量鉄骨' in value:
        return '軽量鉄骨'
    if '鉄骨' in value:
        return '鉄骨'
    if '木造' in value:
        return '木造'
    return str(value or '').strip()


def building_age_from_values(*values):
    now = datetime.now(timezone.utc)
    for raw in values:
        value = unicodedata.normalize('NFKC', str(raw or '')).strip()
        if not value:
            continue
        if '新築' in value:
            return 0, now.year
        match = re.search(r'築\s*(\d{1,3})\s*年', value)
        if match:
            age = int(match.group(1))
            return age, now.year-age
        match = re.search(r'((?:19|20)\d{2})\s*年\s*(\d{1,2})?\s*月?', value)
        if match:
            year = int(match.group(1))
            month = int(match.group(2) or 1)
            age = now.year-year-(1 if now.month < month else 0)
            return max(0, age), year
        if re.fullmatch(r'(?:19|20)\d{2}', value):
            year = int(value)
            return max(0, now.year-year), year
    return None, None


def named_text_value(soup, labels):
    labels = tuple(labels)
    for tag in soup.find_all(['th','dt']):
        label = unicodedata.normalize('NFKC', tag.get_text(' ', strip=True)).replace(' ', '')
        if any(x in label for x in labels):
            sibling = tag.find_next_sibling(['td','dd'])
            if sibling:
                value = sibling.get_text(' ', strip=True)
                if value:
                    return value
    text = unicodedata.normalize('NFKC', soup.get_text(' ', strip=True))
    for label in labels:
        m = re.search(re.escape(label)+r'\s*[:：|]?\s*([^|｜]{1,45})', text)
        if m:
            return m.group(1).strip()
    return ''


def extract_structure_age(soup, entity=None, attrs=None, data=None):
    entity = entity if isinstance(entity, dict) else {}
    attrs = attrs if isinstance(attrs, dict) else {}
    data = data if isinstance(data, dict) else {}
    structure_values = [attrs.get(k) for k in ('建物構造','構造','建物の構造')]
    structure_values += [entity.get('buildingType'), named_text_value(soup, ('建物構造','構造'))]
    structure = ''
    for value in structure_values:
        candidate = normalize_structure(value)
        if candidate:
            structure = candidate
            if candidate == 'SRC':
                break
    age_values = [attrs.get(k) for k in ('築年月','築年数','建築年月','竣工年月')]
    age_values += [entity.get('yearBuilt'), data.get('dateCreated'),
                   named_text_value(soup, ('築年月','築年数','建築年月','竣工年月'))]
    age, year = building_age_from_values(*age_values)
    return structure, age, year


def target_property(p):
    try:
        return (str(p.get('layout','')) in TARGET_LAYOUTS
                and normalize_structure(p.get('structure')) == TARGET_STRUCTURE
                and p.get('building_age') not in ('', None)
                and 0 <= int(float(p.get('building_age'))) <= MAX_BUILDING_AGE)
    except (ValueError, TypeError):
        return False


def layout_matches_group(raw_layout, group):
    return str(raw_layout or '').strip() in LAYOUT_GROUPS.get(group, ())


def parse_listing(html, url, fetched_at, bounds=None, region=None, extra_hints=None):
    soup = BeautifulSoup(html, 'html.parser')
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            parsed = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        for data in json_nodes(parsed):
            if data.get('@type') != 'RealEstateListing':
                continue
            offer, entity = data.get('offers', {}), data.get('mainEntity', {})
            if not isinstance(offer, dict) or not isinstance(entity, dict):
                continue
            if offer.get('availability') not in (None, 'https://schema.org/InStock', 'http://schema.org/InStock'):
                continue
            if offer.get('priceCurrency') != 'JPY':
                continue
            try:
                rent = money(offer['price'])
                size = float(entity.get('floorSize', {}).get('value', 0))
            except (KeyError, ValueError, TypeError):
                continue
            if not (0 < rent <= 10_000_000 and math.isfinite(size) and size > 0):
                continue
            attrs = {a.get('name'): a.get('value') for a in entity.get('additionalProperty', []) if isinstance(a, dict)}
            layout = str(attrs.get('間取り', '')).strip()
            if layout not in STORAGE_LAYOUTS:
                continue
            costs = {a.get('name'): a.get('value') for a in offer.get('additionalProperty', []) if isinstance(a, dict)}
            if '管理費等' not in costs:
                continue
            try:
                fees = money(costs['管理費等'])
                if fees > 10_000_000:
                    continue
            except ValueError:
                continue
            structure, building_age, built_year = extract_structure_age(soup, entity, attrs, data)
            address = entity.get('address', {})
            addr = ''.join(str(address.get(k, '')) for k in ('addressRegion', 'addressLocality', 'streetAddress')) if isinstance(address, dict) else ''
            location = resolve_property_location(html, soup, addr, bounds, region, "HOME'S", extra_hints)
            if not location:
                continue
            image = entity.get('image', [])
            caption = image[0].get('caption') if isinstance(image, list) and image and isinstance(image[0], dict) else None
            canonical = data.get('url', url)
            if not safe_source_url(canonical):
                canonical = url
            return dict(id=canonical.rstrip('/').split('/')[-1], name=caption or entity.get('name', '物件'),
                        address=addr, map_address=location['map_address'], address_match=location['address_match'],
                        location_confidence=location['location_confidence'], lat=location['lat'], lng=location['lng'],
                        layout=layout, rent=rent, fees=fees, area=size, floor=str(entity.get('floorLevel', '')),
                        structure=structure, building_age=building_age if building_age is not None else '',
                        built_year=built_year if built_year is not None else '', coordinate_source=location['coordinate_source'],
                        url=canonical, fetched_at=fetched_at, modified=str(data.get('dateModified', '')),
                        source="LIFULL HOME'S")
    return None


@functools.lru_cache(maxsize=4096)
def fetch_listing(url):
    return parse_listing(fetch_html(url), url, datetime.now(timezone.utc).isoformat(timespec='seconds'))


def unit_key(p):
    # Multiple agencies listing the same room are counted once when observable fields match.
    return (round(p['lat'], 5), round(p['lng'], 5), p['layout'], p['floor'], round(p['area'], 1), p['rent'], p['fees'])


def deduplicate(properties):
    result, seen = [], set()
    for p in properties:
        key = unit_key(p)
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def collect(base, layout, limit=120, pages=3, progress=None):
    if not check_robots():
        raise ValueError('現在、取得元が自動取得を許可していません。CSV読み込みを利用してください。')
    links, warnings, seen_links = [], [], set()
    for page in range(1, pages+1):
        try:
            found = room_links(fetch_html(search_url(base, layout, page)))
        except (requests.RequestException, ValueError) as error:
            warnings.append(f'一覧{page}ページ目を取得できませんでした（{type(error).__name__}）。')
            break
        fresh = [u for u in found if u not in seen_links]
        if not fresh:
            break
        seen_links.update(fresh)
        links.extend(fresh)
        if len(links) >= limit:
            break
    links = links[:limit]
    records, failed = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch_listing, url): url for url in links}
        for number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                p = future.result()
                if p and p['layout'] == layout and target_property(p):
                    records.append(p)
                else:
                    failed += 1
            except (requests.RequestException, ValueError, KeyError, TypeError):
                failed += 1
            if progress:
                progress(number, len(links), len(records))
    records = deduplicate(records)
    if failed:
        warnings.append(f'{failed}ページは位置・金額・間取りを確認できず除外しました。')
    return records, dict(layout=layout, requested=len(links), received=len(records), warnings=warnings,
                         source_url=base, fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def project(lat, lng, origin):
    return ((lng-origin['lng'])*111320*math.cos(math.radians(origin['lat'])), (lat-origin['lat'])*111320)


def unproject(x, y, origin):
    return [origin['lat']+y/111320, origin['lng']+x/(111320*math.cos(math.radians(origin['lat'])))]


def color_for(yen):
    amount = yen/10000
    return COLORS[sum(amount > cut for cut in BANDS)]


def mesh(properties, origin, cell=100, radius=1500, interpolate=True, reach=400, minimum=3):
    """Build rent cells with a small spatial index so large saved datasets do not block UI startup."""
    buildings = {}
    for p in properties:
        x, y = project(p['lat'], p['lng'], origin)
        if math.hypot(x, y) > radius:
            continue
        key = (round(p['lat'], 5), round(p['lng'], 5))
        buildings.setdefault(key, []).append(p)

    observed = {}
    points = []
    bucket_size = max(float(reach), float(cell), 1.0)
    buckets = {}
    for ps in buildings.values():
        x, y = project(ps[0]['lat'], ps[0]['lng'], origin)
        point = dict(x=x, y=y, price=statistics.median(p['rent']+p['fees'] for p in ps), count=len(ps))
        points.append(point)
        observed.setdefault((math.floor(x/cell), math.floor(y/cell)), []).append(point)
        bkey = (math.floor(x/bucket_size), math.floor(y/bucket_size))
        buckets.setdefault(bkey, []).append(point)

    cells = []
    extent = math.ceil(radius/cell)
    bucket_span = max(1, math.ceil(reach/bucket_size))
    for ix in range(-extent, extent):
        for iy in range(-extent, extent):
            cx, cy = (ix+.5)*cell, (iy+.5)*cell
            if math.hypot(cx, cy) > radius:
                continue
            own = observed.get((ix, iy), [])
            if own:
                price = statistics.median(p['price'] for p in own)
                kind, support, farthest = '掲載物件の集計', own, 0
            elif interpolate:
                bx, by = math.floor(cx/bucket_size), math.floor(cy/bucket_size)
                candidates = []
                for dx in range(-bucket_span, bucket_span+1):
                    for dy in range(-bucket_span, bucket_span+1):
                        candidates.extend(buckets.get((bx+dx, by+dy), ()))
                support = []
                for point in candidates:
                    d = math.hypot(point['x']-cx, point['y']-cy)
                    if d <= reach:
                        support.append((point, d))
                if len(support) < minimum:
                    continue
                weighted = [(point['price'], 1/max(50, d)**2) for point, d in support]
                price = sum(value*w for value, w in weighted)/sum(w for _, w in weighted)
                kind = '近隣からの推定'
                farthest = round(max(d for _, d in support))
                support = [point for point, _ in support]
            else:
                continue
            cells.append(dict(bounds=[unproject(ix*cell, iy*cell, origin), unproject((ix+1)*cell, (iy+1)*cell, origin)],
                              price=price, kind=kind, buildings=len(support), rooms=sum(p['count'] for p in support), farthest=farthest))
    return cells


def validate_records(raw, maximum=20000):
    if len(raw) > max(20_000_000, maximum*3500):
        raise ValueError('CSVが大きすぎます。')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    required = {'name', 'lat', 'lng', 'layout', 'rent', 'fees', 'area'}
    if not reader.fieldnames or not required <= set(reader.fieldnames):
        raise ValueError('name, lat, lng, layout, rent, fees, area列が必要です。金額は円、座標は掲載位置を指定します。')
    records = []
    for n, row in enumerate(reader, 2):
        if n > maximum+1:
            raise ValueError(f'CSVは{maximum:,}件以内にしてください。')
        try:
            p = {key: row.get(key, '') for key in REAL_FIELDS}
            for key in ('lat', 'lng', 'area'):
                p[key] = float(row[key])
            for key in ('rent', 'fees'):
                p[key] = int(float(row[key]))
            if p.get('building_age') not in ('', None):
                p['building_age'] = int(float(p['building_age']))
            if p.get('built_year') not in ('', None):
                p['built_year'] = int(float(p['built_year']))
            if not (34 <= p['lat'] <= 37 and 138 <= p['lng'] <= 141 and math.isfinite(p['area']) and p['area'] > 0
                    and 0 < p['rent'] <= 10_000_000 and 0 <= p['fees'] <= 10_000_000 and p['layout'] in STORAGE_LAYOUTS):
                raise ValueError()
            for field in ('name', 'id', 'address', 'map_address', 'address_match', 'location_confidence', 'floor', 'structure', 'coordinate_source'):
                p[field] = str(p.get(field, ''))[:200]
                if p[field].startswith("'") and p[field][1:].startswith(('=', '+', '-', '@')):
                    p[field] = p[field][1:]
            p['name'] = p['name'].strip()
            if not p['name']:
                raise ValueError()
            if p['url'] and (urlparse(p['url']).scheme not in ('http', 'https') or not urlparse(p['url']).netloc):
                raise ValueError()
            p['id'] = p['id'] or str(uuid.uuid4())
            p['source'] = p['source'] or 'CSV入力'
            records.append(p)
        except (ValueError, TypeError, KeyError):
            raise ValueError(f'{n}行目の金額・位置・間取りを確認してください。') from None
    return deduplicate(records)


def export_records(records):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=REAL_FIELDS)
    writer.writeheader()
    for p in records:
        row = {k: p.get(k, '') for k in REAL_FIELDS}
        for k, value in row.items():
            if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')):
                row[k] = "'"+value
        writer.writerow(row)
    return output.getvalue().encode('utf-8-sig')


@functools.lru_cache(maxsize=128)
def fetch_facilities_at(lat, lng, category):
    query = f'[out:json][timeout:15];nwr{FACILITY_FILTERS[category]}(around:1000,{lat},{lng});out center tags;'
    r = requests.post('https://overpass-api.de/api/interpreter', data={'data': query}, timeout=(5, 20))
    r.raise_for_status()
    facilities = []
    for e in r.json().get('elements', []):
        loc = e.get('center', {})
        x, y = e.get('lat', loc.get('lat')), e.get('lon', loc.get('lon'))
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            facilities.append(dict(lat=x, lng=y, name=e.get('tags', {}).get('name', '名称未登録')))
    return facilities[:100]


def make_map(origin, cells, records, facilities):
    import folium
    m = folium.Map(location=[origin['lat'], origin['lng']], zoom_start=st.session_state.get('zoom03', 15), tiles='OpenStreetMap', control_scale=True, prefer_canvas=True)
    for c in cells:
        estimated = c['kind'] == '近隣からの推定'
        tip = (f'{c["price"]/10000:.1f}万円/月 · {c["kind"]}<br>'
               f'根拠：{c["buildings"]}建物 / {c["rooms"]}募集住戸'
               +(f'<br>最遠の参照建物：{c["farthest"]}m' if estimated else ''))
        folium.Rectangle(c['bounds'], color=color_for(c['price']), weight=.5, fill=True,
                         fill_color=color_for(c['price']), fill_opacity=.36 if estimated else .65,
                         dash_array='3,3' if estimated else None, tooltip=tip).add_to(m)
    for p in records:
        url = p['url']
        safe = urlparse(url).scheme in ('http', 'https') and bool(urlparse(url).netloc)
        detail = (f'<b>{escape(p["name"])}</b><br>{escape(p["layout"])} / {p["area"]:g}㎡ / {escape(p["floor"])}<br>'
                  f'総額 {(p["rent"]+p["fees"])/10000:g}万円（月額）<br>'
                  f'家賃 {p["rent"]:,}円 ＋ 管理費等 {p["fees"]:,}円<br>掲載住所：{escape(p.get("address",""))}<br>'
                  f'地図判定：{escape(p.get("map_address",""))} / 精度 {escape(p.get("location_confidence",""))}<br>'
                  +(f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener">募集ページを確認</a>' if safe else 'CSV入力'))
        folium.CircleMarker([p['lat'], p['lng']], radius=5, color='white', weight=1,
                            fill=True, fill_color=color_for(p['rent']+p['fees']), fill_opacity=1,
                            tooltip=f'{escape(p["name"])} · {(p["rent"]+p["fees"])/10000:g}万',
                            popup=folium.Popup(detail, max_width=300)).add_to(m)

    for f in facilities:
        folium.CircleMarker([f['lat'], f['lng']], radius=6, fill=True, fill_color='#173a5e', fill_opacity=1,
                            color='white', weight=1, tooltip=escape(f['name'])).add_to(m)
    return m

def normalize_bounds(raw):
    try:
        if isinstance(raw, dict):
            a, b = raw['_southWest'], raw['_northEast']
            values = [float(a['lat']), float(a['lng']), float(b['lat']), float(b['lng'])]
        else:
            values = list(map(float, raw))
        south, west, north, east = values
        if not all(math.isfinite(x) for x in values) or not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
            return None
        return values
    except (TypeError, KeyError, ValueError):
        return None


def inside(p, bounds):
    return bool(bounds and bounds[0] <= p['lat'] <= bounds[2] and bounds[1] <= p['lng'] <= bounds[3])


def cell_intersects(c, b):
    sw, ne = c['bounds']
    return sw[0] <= b[2] and ne[0] >= b[0] and sw[1] <= b[3] and ne[1] >= b[1]


def bounds_radius(b, origin):
    return max(distance(origin, dict(lat=lat,lng=lng))*1000 for lat in (b[0],b[2]) for lng in (b[1],b[3]))


class StorageError(Exception):
    """Safe, actionable messages; never expose provider response bodies or credentials."""


def secret_value(name, default=''):
    value = os.environ.get(name)
    if value is None:
        try:
            value = st.secrets.get(name, default)
        except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            value = default
    return str(value or '').strip()


def supabase_settings():
    url = secret_value('SUPABASE_URL').rstrip('/')
    key = secret_value('SUPABASE_SECRET_KEY') or secret_value('SUPABASE_SERVICE_ROLE_KEY')
    namespace = secret_value('SUPABASE_NAMESPACE', 'sumai-compass')
    if not url or not key:
        raise StorageError('StreamlitのSecretsにSUPABASE_URLとSUPABASE_SECRET_KEYを設定してください。')
    try:
        parsed = urlparse(url)
        valid_url = (parsed.scheme == 'https' and parsed.hostname and parsed.hostname.endswith('.supabase.co')
                     and not parsed.username and not parsed.password and parsed.port in (None,443)
                     and parsed.path in ('','/') and not parsed.query and not parsed.fragment)
    except ValueError:
        valid_url = False
    if not valid_url:
        raise StorageError('SUPABASE_URLには https://プロジェクトID.supabase.co 形式のProject URLを設定してください。')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', namespace):
        raise StorageError('SUPABASE_NAMESPACEは英数字・ハイフン・アンダースコアで80文字以内にしてください。')
    headers = {'apikey':key, 'Content-Type':'application/json', 'Accept':'application/json'}
    if key.startswith('sb_secret_'):
        pass  # New secret keys use apikey, not Authorization: Bearer.
    else:
        try:
            encoded = key.split('.')[1]
            payload = json.loads(base64.urlsafe_b64decode(encoded + '='*(-len(encoded)%4)))
            if len(key.split('.')) != 3 or payload.get('role') != 'service_role':
                raise ValueError()
        except (ValueError, IndexError, UnicodeError, TypeError, AttributeError):
            raise StorageError('Secret key（sb_secret_…）または旧service_roleキーを使用してください。anon/publishableキーでは保存できません。') from None
        headers['Authorization'] = 'Bearer '+key
    return url, headers, namespace


_SUPABASE_HTTP = threading.local()


def supabase_session():
    if not hasattr(_SUPABASE_HTTP, 'session'):
        _SUPABASE_HTTP.session = requests.Session()
    return _SUPABASE_HTTP.session


def supabase_request(method, route, *, params=None, payload=None, settings=None,
                     extra_headers=None, allow_empty=False):
    url, headers, _ = settings or supabase_settings()
    allowed = ('sumai_properties','sumai_fetch_queries','sumai_metadata','sumai_jobs',
               'rpc/sumai_save_properties')
    if route not in allowed:
        raise StorageError('保存先の指定が不正です。')
    headers = dict(headers)
    if extra_headers:
        headers.update(extra_headers)
    try:
        response = supabase_session().request(
            method, url+'/rest/v1/'+route, headers=headers,
            params=params, json=payload, timeout=(5,25), allow_redirects=False)
    except requests.RequestException:
        raise StorageError('Supabaseに接続できませんでした。Projectの稼働状態・接続設定を確認してください。') from None
    if not 200 <= response.status_code < 300:
        if response.status_code in (401,403):
            message = 'Supabaseのキーまたはアクセス権が不正です。Secret keyを確認してください。'
        elif response.status_code == 404:
            message = 'Supabase v05のテーブル/列がありません。supabase_setup_ver.05.sqlをSQL Editorで実行してください。'
        else:
            message = f'Supabaseの読み込み・保存に失敗しました（HTTP {response.status_code}）。'
        raise StorageError(message)
    if not response.content.strip():
        return {} if allow_empty else None
    try:
        return response.json()
    except ValueError:
        if allow_empty:
            return {}
        raise StorageError('Supabaseの応答を読み取れませんでした。') from None


def ensure_schema_v05(settings=None):
    settings = settings or supabase_settings()
    rows = supabase_request('GET','sumai_jobs',settings=settings,
                            params={'select':'job_id','limit':1})
    if rows is not None and not isinstance(rows, list):
        raise StorageError('Supabase v05のジョブテーブルを確認できません。')
    return True


def _payload_record(row):
    if not isinstance(row, dict):
        return None
    p = row.get('payload') if isinstance(row.get('payload'), dict) else row
    if not isinstance(p, dict):
        return None
    try:
        p = dict(p)
        p['lat'] = float(p['lat']); p['lng'] = float(p['lng'])
        p['rent'] = int(float(p['rent'])); p['fees'] = int(float(p.get('fees',0)))
        p['area'] = float(p['area'])
        if p.get('building_age') not in ('', None):
            p['building_age'] = int(float(p['building_age']))
        return p
    except (KeyError, TypeError, ValueError):
        return None


def expand_bounds(bounds, meters=0):
    b = normalize_bounds(bounds)
    if not b or meters <= 0:
        return b
    south, west, north, east = b
    mid = (south+north)/2
    dlat = meters/111320
    dlng = meters/(111320*max(.2, math.cos(math.radians(mid))))
    return [south-dlat, west-dlng, north+dlat, east+dlng]


def load_viewport_store(bounds, layouts=None, settings=None, limit=3000, padding_m=0):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    b = expand_bounds(bounds, padding_m)
    if not b:
        return []
    south, west, north, east = b
    params = {
        'namespace':'eq.'+namespace,
        'select':'payload',
        'and':f'(lat.gte.{south},lat.lte.{north},lng.gte.{west},lng.lte.{east})',
        'structure':'eq.SRC',
        'building_age':f'lte.{MAX_BUILDING_AGE}',
        'order':'fetched_at.desc',
        'limit':max(1,min(int(limit),5000)),
    }
    if layouts:
        safe = [x for x in layouts if x in STORAGE_LAYOUTS]
        if safe:
            params['layout'] = 'in.(' + ','.join(safe) + ')'
    rows = supabase_request('GET','sumai_properties',settings=settings,params=params)
    if not isinstance(rows, list):
        raise StorageError('Supabaseの表示範囲データを読み取れませんでした。')
    records = []
    for row in rows:
        p = _payload_record(row)
        if p and target_property(p):
            records.append(p)
    return deduplicate(records)


def load_all_store(settings=None, maximum=20000):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    records, offset = [], 0
    maximum = max(1, min(int(maximum), 50000))
    while offset < maximum:
        batch = min(1000, maximum-offset)
        rows = supabase_request('GET','sumai_properties',settings=settings,params={
            'namespace':'eq.'+namespace, 'select':'payload', 'order':'fetched_at.desc',
            'limit':batch, 'offset':offset})
        if not isinstance(rows,list):
            raise StorageError('Supabaseの物件データ形式が不正です。')
        if not rows:
            break
        for row in rows:
            p = _payload_record(row)
            if p:
                records.append(p)
        offset += len(rows)
        if len(rows) < batch:
            break
    meta = supabase_request('GET','sumai_metadata',settings=settings,params={
        'namespace':'eq.'+namespace,'select':'payload','identity':'eq.last_fetch','limit':1})
    info = meta[0]['payload'] if isinstance(meta,list) and meta and isinstance(meta[0].get('payload'),dict) else {}
    return deduplicate(records), info


def query_key(bounds, layout, limit):
    value = json.dumps([BACKGROUND_ENGINE_VERSION, list(map(lambda x: round(x,5), bounds)),
                        'ALL_TARGET_LAYOUTS', int(limit)], ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def cached_query(bounds, layout, limit, settings=None):
    _, _, namespace = settings or supabase_settings()
    rows = supabase_request('GET','sumai_fetch_queries',settings=settings,params={
        'namespace':'eq.'+namespace,'identity':'eq.'+query_key(bounds,layout,limit),'select':'payload','limit':1})
    if not isinstance(rows,list) or (rows and not isinstance(rows[0].get('payload'),dict)):
        raise StorageError('Supabaseの範囲キャッシュの形式が不正です。')
    return rows[0]['payload'] if rows else None


def _property_row(p, namespace):
    return {
        'namespace':namespace,
        'identity':p.get('url') or str(p.get('id','')),
        'payload':p,
        'fetched_at':p['fetched_at'],
        'source':p.get('source',''),
        'listing_id':str(p.get('id','')),
        'name':p.get('name',''),
        'address':p.get('address',''),
        'map_address':p.get('map_address',''),
        'lat':float(p['lat']), 'lng':float(p['lng']),
        'layout':p.get('layout',''),
        'rent':int(p['rent']), 'fees':int(p.get('fees',0)), 'area':float(p['area']),
        'floor':p.get('floor',''), 'structure':p.get('structure',''),
        'building_age':int(p['building_age']) if p.get('building_age') not in ('',None) else None,
        'built_year':int(p['built_year']) if p.get('built_year') not in ('',None) else None,
        'coordinate_source':p.get('coordinate_source',''),
        'location_confidence':p.get('location_confidence',''),
        'url':p.get('url',''), 'modified':p.get('modified',''),
    }


def _upsert_rows(route, rows, conflict, settings, batch_size=100):
    if not rows:
        return
    for start in range(0, len(rows), batch_size):
        supabase_request('POST',route,settings=settings,
                         params={'on_conflict':conflict}, payload=rows[start:start+batch_size],
                         extra_headers={'Prefer':'resolution=merge-duplicates,return=minimal'}, allow_empty=True)


def save_metadata(identity, payload, settings=None):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    _upsert_rows('sumai_metadata',[{'namespace':namespace,'identity':identity,'payload':payload}],
                 'namespace,identity',settings)


def save_store(records, info, bounds=None, layout=None, limit=None, reload_after=False,
               settings=None, mark_query_cache=True):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    try:
        records = validate_records(export_records(records),maximum=max(20000,len(records)+10))
    except (ValueError, KeyError, TypeError, csv.Error):
        raise StorageError('保存する物件データの形式を確認してください。') from None
    if not records:
        raise StorageError('保存する物件がありません。')
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    unique = {}
    for p in records:
        p = dict(p)
        stamp = p.get('fetched_at') or now
        try:
            parsed = datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            p['fetched_at'] = parsed.astimezone(timezone.utc).isoformat(timespec='seconds')
        except (ValueError, TypeError, AttributeError):
            raise StorageError('物件の取得日時を確認してください。') from None
        identity = p.get('url') or str(p.get('id',''))
        if identity:
            unique[identity] = p
    rows = [_property_row(p, namespace) for p in unique.values()]
    _upsert_rows('sumai_properties',rows,'namespace,identity',settings,batch_size=25)
    save_metadata('last_fetch',info,settings)
    if bounds is not None and mark_query_cache:
        _upsert_rows('sumai_fetch_queries',[{
            'namespace':namespace,'identity':query_key(bounds,layout,limit),'payload':info}],
            'namespace,identity',settings)
    return list(unique.values())


def save_incremental_records(records, settings=None):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    try:
        records = validate_records(export_records(records),maximum=max(50,len(records)+5))
    except (ValueError, KeyError, TypeError, csv.Error):
        raise StorageError('保存する物件データの形式を確認してください。') from None
    if not records:
        return []
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    unique = {}
    for p in records:
        p = dict(p)
        stamp = p.get('fetched_at') or now
        parsed = datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        p['fetched_at'] = parsed.astimezone(timezone.utc).isoformat(timespec='seconds')
        identity = p.get('url') or str(p.get('id',''))
        if identity:
            unique[identity] = p
    rows = [_property_row(p,namespace) for p in unique.values()]
    _upsert_rows('sumai_properties',rows,'namespace,identity',settings,batch_size=25)
    return list(unique)


def job_get(job_id, settings=None):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    rows = supabase_request('GET','sumai_jobs',settings=settings,params={
        'namespace':'eq.'+namespace,'job_id':'eq.'+job_id,'select':'*','limit':1})
    return rows[0] if isinstance(rows,list) and rows else {}


def latest_job(settings=None):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    rows = supabase_request('GET','sumai_jobs',settings=settings,params={
        'namespace':'eq.'+namespace,'select':'*','order':'updated_at.desc','limit':1})
    return rows[0] if isinstance(rows,list) and rows else {}


def job_upsert(job_id, values, settings=None):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    row = {'namespace':namespace,'job_id':job_id,'updated_at':now}
    row.update(values)
    _upsert_rows('sumai_jobs',[row],'namespace,job_id',settings)
    return row


def job_cancel(job_id, settings=None):
    settings = settings or supabase_settings()
    _, _, namespace = settings
    supabase_request('PATCH','sumai_jobs',settings=settings,
                     params={'namespace':'eq.'+namespace,'job_id':'eq.'+job_id},
                     payload={'cancel_requested':True,'updated_at':datetime.now(timezone.utc).isoformat(timespec='seconds')},
                     extra_headers={'Prefer':'return=minimal'},allow_empty=True)


def show_storage_setup():
    st.info('保存・読み込み先はSupabaseです。接続設定が完了すると地図を表示します。')
    st.markdown('1. SupabaseのSQL Editorで **supabase_setup_ver.05.sql** を実行。\n'
                '2. Streamlitの **Settings → Secrets** に下の接続情報を登録。\n'
                '3. アプリを再起動、または下のボタンで再接続。')
    st.code('SUPABASE_URL = "https://プロジェクトID.supabase.co"\n'
            'SUPABASE_SECRET_KEY = "sb_secret_から始まるSecret key"\n'
            'SUPABASE_NAMESPACE = "sumai-compass"',language='toml')
    st.caption('キーはStreamlitのサーバーからのみ使用します。GitHubやチャットには貼り付けないでください。旧service_roleキーはSUPABASE_SERVICE_ROLE_KEYでも指定できます。')
    if st.button('Supabaseに再接続',key='reconnect04'):
        st.rerun()


MAP_TILE_URL = 'https://www.homes.co.jp/_ajax/map/realestate_article/tile/'
MAP_INFO_URL = 'https://www.homes.co.jp/_ajax/map/realestate_article/info_view/'
SUUMO_SEARCH_URL = 'https://suumo.jp/jj/chintai/ichiran/FR301FC001/'
SUUMO_MD_CODES = {
    '1K':'02', '1DK':'03', '1LDK':'04',
    '2DK':'06', '2LDK':'07',
    '3DK':'09', '3LDK':'10',
}
GSI_REVERSE_URL = 'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress'
GSI_SEARCH_URL = 'https://msearch.gsi.go.jp/address-search/AddressSearch'
GSI_MUNI_URL = 'https://maps.gsi.go.jp/js/muni.js'
SUUMO_HEADERS = {'User-Agent': 'SumaiCompass/8.0 (personal rental map)'}
GSI_HEADERS = {'User-Agent': 'SumaiCompass/8.0 (personal rental map)'}
_SUUMO_HTTP = threading.local()

# Network work is I/O-bound, but nested parallelism must not starve Streamlit's
# websocket/custom-component traffic. Crawling starts only after an explicit user action.
BACKGROUND_ENGINE_VERSION = 'v15-process-worker-indexed'
WORKER_HEARTBEAT_SECONDS = 8
WORKER_STALE_SECONDS = 180
REGION_LOOKUP_WORKERS = 6
REGION_TASK_WORKERS = 3
HOMES_BUILDING_WORKERS = 4
HOMES_DETAIL_WORKERS = 4
SUUMO_DETAIL_WORKERS = 4
HOMES_REQUEST_SLOTS = 6
SUUMO_REQUEST_SLOTS = 5
GSI_REQUEST_SLOTS = 5
_HOMES_REQUEST_SEMAPHORE = threading.BoundedSemaphore(HOMES_REQUEST_SLOTS)
_SUUMO_REQUEST_SEMAPHORE = threading.BoundedSemaphore(SUUMO_REQUEST_SLOTS)
_GSI_REQUEST_SEMAPHORE = threading.BoundedSemaphore(GSI_REQUEST_SLOTS)


def suumo_session():
    if not hasattr(_SUUMO_HTTP, 'session'):
        _SUUMO_HTTP.session = requests.Session()
    return _SUUMO_HTTP.session


@functools.lru_cache(maxsize=1)
def gsi_municipalities():
    try:
        with _GSI_REQUEST_SEMAPHORE:
            r = requests.get(GSI_MUNI_URL, headers=GSI_HEADERS, timeout=(5, 15), allow_redirects=False)
        r.raise_for_status()
        if len(r.content) > 3_000_000:
            return {}
        # muni.js is UTF-8, but its HTTP headers may not declare an encoding.
        # Decode bytes explicitly to prevent Japanese place-name mojibake.
        text = r.content.decode('utf-8-sig')
        table = {}
        for code, value in re.findall(r'GSI\.MUNI_ARRAY\["(\d+)"\]\s*=\s*[\'\"]([^\'\"]+)', text):
            parts = value.split(',')
            if len(parts) >= 4:
                table[code] = {'pref_code': parts[0], 'prefecture': parts[1], 'municipality': parts[3]}
        return table
    except (requests.RequestException, ValueError, TypeError, UnicodeError):
        return {}


@functools.lru_cache(maxsize=8192)
def gsi_reverse_region(lat, lng):
    try:
        with _GSI_REQUEST_SEMAPHORE:
            r = requests.get(GSI_REVERSE_URL, params={'lat': round(float(lat), 7), 'lon': round(float(lng), 7)},
                             headers=GSI_HEADERS, timeout=(5, 15), allow_redirects=False)
        r.raise_for_status()
        if len(r.content) > 500_000:
            return None
        data = json.loads(r.content.decode('utf-8-sig')).get('results', {})
        code, town = str(data.get('muniCd', '')).strip(), str(data.get('lv01Nm', '')).strip()
        if not code or not town:
            return None
        muni = gsi_municipalities().get(code, {})
        town_base = re.sub(r'[0-9０-９一二三四五六七八九十百]+丁目$', '', town).strip() or town
        prefecture = muni.get('prefecture', '')
        municipality = muni.get('municipality', '')
        # Preserve the chome in the visible-region label and search key.
        label = ''.join(x for x in (prefecture, municipality, town) if x)
        return {'muni_code': code, 'pref_code': muni.get('pref_code', ''), 'prefecture': prefecture,
                'municipality': municipality, 'town': town, 'town_base': town_base,
                'label': label or town, 'lat': float(lat), 'lng': float(lng)}
    except (requests.RequestException, ValueError, TypeError, AttributeError, UnicodeError):
        return None


def _japanese_number(text):
    text = unicodedata.normalize('NFKC', str(text or ''))
    if text.isdigit():
        return int(text)
    digits = {'〇':0,'零':0,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    units = {'十':10, '百':100}
    total, current = 0, 0
    for ch in text:
        if ch in digits:
            current = digits[ch]
        elif ch in units:
            total += (current or 1) * units[ch]
            current = 0
        else:
            return None
    return total + current if total or current else None


def _normalize_place_text(value):
    text = unicodedata.normalize('NFKC', str(value or '')).replace(' ', '').replace('　', '')
    def repl(match):
        number = _japanese_number(match.group(1))
        return (str(number) if number is not None else match.group(1)) + '丁目'
    return re.sub(r'([0-9一二三四五六七八九十百]+)丁目', repl, text)


def region_search_terms(region):
    town = str(region.get('town', '')).strip()
    base = str(region.get('town_base', '')).strip() or town
    terms = []
    for value in (town, _normalize_place_text(town), base):
        value = str(value).strip()
        if value and value not in terms:
            terms.append(value)
    return terms


def region_matches_address(address, region):
    address_n = _normalize_place_text(address)
    town_n = _normalize_place_text(region.get('town', ''))
    base_n = _normalize_place_text(region.get('town_base', ''))
    # When a chome is known, keep only that chome. Otherwise compare the town name.
    target = town_n if town_n and town_n != base_n else base_n
    return bool(target and target in address_n)


def visible_regions(bounds, should_stop=None):
    bounds = normalize_bounds(bounds)
    if not bounds:
        raise ValueError('地図の表示範囲を確認できません。')
    if should_stop and should_stop():
        return []
    south, west, north, east = bounds
    mid_lat = (south+north)/2
    height_m = max(1.0, (north-south)*111320)
    width_m = max(1.0, (east-west)*111320*math.cos(math.radians(mid_lat)))
    rows = min(28, max(3, math.ceil(height_m/300)))
    cols = min(28, max(3, math.ceil(width_m/300)))
    points = []
    for iy in range(rows):
        fy = (iy + .5) / rows
        lat = south + (north-south)*fy
        for ix in range(cols):
            fx = (ix + .5) / cols
            lng = west + (east-west)*fx
            points.append((lat, lng))
    regions, seen = [], set()
    def lookup(xy):
        if should_stop and should_stop():
            return None
        return gsi_reverse_region(xy[0], xy[1])
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(REGION_LOOKUP_WORKERS, len(points)))
    jobs = [pool.submit(lookup, xy) for xy in points]
    try:
        for job in concurrent.futures.as_completed(jobs):
            if should_stop and should_stop():
                for pending in jobs:
                    pending.cancel()
                break
            try:
                region = job.result()
            except Exception:
                region = None
            if not region:
                continue
            key = (region['muni_code'], _normalize_place_text(region['town']))
            if key in seen:
                continue
            seen.add(key)
            regions.append(region)
            if len(regions) >= 250:
                for pending in jobs:
                    pending.cancel()
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not regions and not (should_stop and should_stop()):
        raise ValueError('表示範囲の地域名を取得できませんでした。地図を少し動かして再試行してください。')
    return regions

def gsi_geocode_address(address):
    address = str(address).strip()
    if not address:
        return None
    try:
        with _GSI_REQUEST_SEMAPHORE:
            r = requests.get(GSI_SEARCH_URL, params={'q': address}, headers=GSI_HEADERS,
                             timeout=(5, 15), allow_redirects=False)
        r.raise_for_status()
        if len(r.content) > 2_000_000:
            return None
        rows = json.loads(r.content.decode('utf-8-sig'))
        if not isinstance(rows, list):
            return None
        normalized = unicodedata.normalize('NFKC', address).replace(' ', '')
        candidates = []
        for item in rows[:8]:
            try:
                coords = item['geometry']['coordinates']
                title = str(item.get('properties', {}).get('title', ''))
                lng, lat = float(coords[0]), float(coords[1])
                if 34 <= lat <= 37 and 138 <= lng <= 141:
                    nt = unicodedata.normalize('NFKC', title).replace(' ', '')
                    score = len(os.path.commonprefix([normalized, nt]))
                    candidates.append((score, lat, lng, title))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        if not candidates:
            return None
        _, lat, lng, title = max(candidates, key=lambda x: x[0])
        return {'lat': lat, 'lng': lng, 'title': title}
    except (requests.RequestException, ValueError, TypeError, AttributeError, UnicodeError):
        return None


def _compact_address(value):
    return re.sub(r'[\s　]+', '', unicodedata.normalize('NFKC', str(value or '')))


def _address_is_precise(value):
    text = _compact_address(value)
    if not text:
        return False
    return bool(
        re.search(r'丁目\d+', text)
        or re.search(r'\d+(?:-|−|ー)\d+', text)
        or re.search(r'\d+番(?:地)?(?:\d+)?', text)
        or re.search(r'\d+号', text)
    )


def _address_match_status(address, reverse_region):
    if not reverse_region:
        return '未確認'
    if not address:
        return '地図位置のみ'
    if region_matches_address(address, reverse_region):
        return '一致'
    address_n = _normalize_place_text(address)
    base = _normalize_place_text(reverse_region.get('town_base', ''))
    muni = _normalize_place_text(reverse_region.get('municipality', ''))
    if base and base in address_n and (not muni or muni in address_n):
        return '一部一致'
    return '不一致'


def _append_coordinate_candidate(out, lat, lng, source, priority):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return
    if not (34 <= lat <= 37 and 138 <= lng <= 141 and math.isfinite(lat) and math.isfinite(lng)):
        return
    key = (round(lat, 7), round(lng, 7), source)
    if key not in {(round(x['lat'],7), round(x['lng'],7), x['source']) for x in out}:
        out.append({'lat':lat, 'lng':lng, 'source':source, 'priority':int(priority)})


def _coordinates_from_url(value, out, source='掲載地図URL'):
    raw = str(value or '')
    if not raw:
        return
    decoded = raw.replace('&amp;', '&')
    # Google/OSM-style paths and embed parameters.
    for m in re.finditer(r'@\s*(3[4-7]\.\d+)\s*,\s*(1(?:3[8-9]|40)\.\d+)', decoded):
        _append_coordinate_candidate(out, m.group(1), m.group(2), source, 98)
    for m in re.finditer(r'!3d(3[4-7]\.\d+)!4d(1(?:3[8-9]|40)\.\d+)', decoded):
        _append_coordinate_candidate(out, m.group(1), m.group(2), source, 98)
    try:
        parsed = urlparse(decoded if '://' in decoded else 'https://dummy.invalid/?'+decoded.lstrip('?'))
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        lat = next((params[k] for k in ('lat','latitude','mlat') if k in params), None)
        lng = next((params[k] for k in ('lng','lon','longitude','mlon') if k in params), None)
        if lat is not None and lng is not None:
            _append_coordinate_candidate(out, lat, lng, source, 98)
        for key in ('q','query','ll','center','sll','cp'):
            if key not in params:
                continue
            m = re.search(r'(3[4-7]\.\d+)\s*[, ]\s*(1(?:3[8-9]|40)\.\d+)', str(params[key]))
            if m:
                _append_coordinate_candidate(out, m.group(1), m.group(2), source, 98)
    except (ValueError, TypeError):
        pass


def extract_coordinate_candidates(html, soup, extra_hints=None):
    out = []
    # Structured data is normally the most reliable source.
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or script.get_text())
            for lat,lng in _json_coordinates(data):
                _append_coordinate_candidate(out, lat, lng, '掲載JSON-LD座標', 100)
        except (ValueError, TypeError):
            pass

    # Explicit map/data attributes on the listing page.
    for tag in soup.find_all(True):
        attrs = tag.attrs if isinstance(tag.attrs, dict) else {}
        lowered = {str(k).lower(): v for k,v in attrs.items()}
        lat = next((lowered[k] for k in ('data-lat','data-latitude','lat','latitude') if k in lowered), None)
        lng = next((lowered[k] for k in ('data-lng','data-lon','data-longitude','lng','lon','longitude') if k in lowered), None)
        if isinstance(lat, (str,int,float)) and isinstance(lng, (str,int,float)):
            _append_coordinate_candidate(out, lat, lng, '掲載地図データ属性', 99)
        for key in ('href','src','data-src','data-url','data-map-url'):
            value = attrs.get(key)
            if isinstance(value, str) and any(token in value.lower() for token in ('map','lat','lon','lng','@','center','query=')):
                _coordinates_from_url(value, out, '掲載地図リンク座標')

    # JavaScript variables used by map widgets. Keep these below explicit map sources.
    normalized = unicodedata.normalize('NFKC', html)
    patterns = [
        r'["\'](?:lat|latitude|maplat|map_lat)["\']?\s*[:=]\s*["\']?(3[4-7]\.\d+)["\']?.{0,400}?["\']?(?:lng|lon|longitude|maplng|map_lng)["\']?\s*[:=]\s*["\']?(1(?:3[8-9]|40)\.\d+)',
        r'["\']?(?:lng|lon|longitude|maplng|map_lng)["\']?\s*[:=]\s*["\']?(1(?:3[8-9]|40)\.\d+)["\']?.{0,400}?["\']?(?:lat|latitude|maplat|map_lat)["\']?\s*[:=]\s*["\']?(3[4-7]\.\d+)'
    ]
    for i,pat in enumerate(patterns):
        for m in re.finditer(pat, normalized, flags=re.I|re.S):
            a,b = float(m.group(1)), float(m.group(2))
            _append_coordinate_candidate(out, a if i == 0 else b, b if i == 0 else a, '掲載ページ埋込地図座標', 90)

    for hint in extra_hints or []:
        if isinstance(hint, dict):
            _append_coordinate_candidate(out, hint.get('lat'), hint.get('lng'),
                                         hint.get('source') or '検索地図座標', hint.get('priority', 97))
        elif isinstance(hint, (tuple,list)) and len(hint) >= 2:
            _append_coordinate_candidate(out, hint[0], hint[1], '検索地図座標', 97)
    return out


def resolve_property_location(html, soup, address, bounds, region, provider, extra_hints=None):
    candidates = extract_coordinate_candidates(html, soup, extra_hints)
    geocoded = gsi_geocode_address(address) if _address_is_precise(address) else None
    ranked = []
    for c in candidates:
        point = {'lat':c['lat'], 'lng':c['lng']}
        if bounds and not inside(point, bounds):
            continue
        # A coordinate tied to another town/ward on the same page is usually a station or agency marker.
        if region and distance(point, region) > 6.0:
            continue
        score = c['priority']
        if geocoded:
            delta = distance(point, geocoded)
            if delta <= .10:
                score += 35
            elif delta <= .30:
                score += 22
            elif delta <= .80:
                score += 8
            elif delta > 2.0:
                score -= 35
        ranked.append((score, c))
    ranked.sort(key=lambda x:x[0], reverse=True)

    # Reverse-geocode the strongest map candidates and reject clear town/address conflicts.
    for score,c in ranked[:12]:
        rev = gsi_reverse_region(c['lat'], c['lng'])
        match = _address_match_status(address, rev)
        if region and rev:
            same_muni = str(rev.get('muni_code','')) == str(region.get('muni_code',''))
            same_town = (_normalize_place_text(rev.get('town','')) == _normalize_place_text(region.get('town',''))
                         or _normalize_place_text(rev.get('town_base','')) == _normalize_place_text(region.get('town_base','')))
            if not same_muni or not same_town:
                score -= 90
        if match == '一致':
            score += 30
        elif match == '一部一致':
            score += 15
        elif match == '不一致':
            score -= 80
        if score < 75:
            continue
        confidence = '高' if c['priority'] >= 97 and match in ('一致','一部一致','地図位置のみ') else '中'
        map_address = rev.get('label','') if rev else ''
        return {'lat':c['lat'], 'lng':c['lng'], 'coordinate_source':f'{provider}:{c["source"]}',
                'map_address':map_address, 'address_match':match, 'location_confidence':confidence}

    # Last resort: use the national address search only when the listing contains a sufficiently precise address.
    # Town-only addresses are intentionally not placed at a representative centroid.
    if geocoded:
        point = {'lat':geocoded['lat'], 'lng':geocoded['lng']}
        if (not bounds or inside(point,bounds)) and (not region or distance(point,region) <= 6.0):
            rev = gsi_reverse_region(point['lat'], point['lng'])
            match = _address_match_status(address, rev)
            region_ok = True
            if region and rev:
                region_ok = (str(rev.get('muni_code','')) == str(region.get('muni_code','')) and
                             (_normalize_place_text(rev.get('town_base','')) == _normalize_place_text(region.get('town_base',''))))
            if region_ok and match in ('一致','一部一致'):
                return {'lat':point['lat'], 'lng':point['lng'],
                        'coordinate_source':'国土地理院住所検索（掲載住所から位置補完）',
                        'map_address':rev.get('label','') if rev else geocoded.get('title',''),
                        'address_match':match, 'location_confidence':'中'}
    return None


@functools.lru_cache(maxsize=1)
def map_robots_allowed():
    with _HOMES_REQUEST_SEMAPHORE:
        response = http_session().get('https://www.homes.co.jp/robots.txt',headers=HEADERS,timeout=(20,20))
    response.raise_for_status()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(response.text.splitlines())
    return all(rp.can_fetch('SumaiCompass', u) for u in (MAP_TILE_URL, MAP_INFO_URL, DEFAULT_URL))


@functools.lru_cache(maxsize=1)
def suumo_robots_allowed():
    with _SUUMO_REQUEST_SEMAPHORE:
        response = suumo_session().get('https://suumo.jp/robots.txt', headers=SUUMO_HEADERS, timeout=(10,20))
    response.raise_for_status()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(response.text.splitlines())
    return rp.can_fetch('SumaiCompass', SUUMO_SEARCH_URL)


def tile_xy(lat, lng, zoom=15):
    n = 2**zoom
    return int((lng/360+.5)*n), int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)


def map_request(url, data):
    if url not in (MAP_TILE_URL, MAP_INFO_URL):
        raise ValueError('取得先が不正です。')
    with _HOMES_REQUEST_SEMAPHORE:
        r = http_session().post(url,data=data,headers=HEADERS,timeout=(20,20),allow_redirects=False)
    if 300 <= r.status_code < 400:
        raise ValueError('取得元が別ページへ移動しました。')
    r.raise_for_status()
    if len(r.content) > 4_000_000:
        raise ValueError('応答が大きすぎます。')
    r.encoding = 'utf-8'
    return r


def map_condition(layout, freeword=''):
    data = {'cond[mbg][3001]':'3001','cond[mbg][3002]':'3002','cond[mbg][3003]':'3003',
            'cond[monthmoneyroom]':'0','cond[monthmoneyroomh]':'0','cond[housearea]':'0',
            'cond[houseareah]':'0','cond[walkminutesh]':'0','cond[houseageh]':str(MAX_BUILDING_AGE),
            'cond[newdate]':'0','cond[freeword]':freeword,'cond[fwtype]':'1','cond[exfreeword]':''}
    if layout in (None, 'ALL'):
        for code in MD_CODES.values():
            data[f'cond[madori][{code}]'] = code
    else:
        code = MD_CODES.get(layout)
        if code:
            data[f'cond[madori][{code}]'] = code
    return data


def viewport_tiles(bounds, zoom=15):
    xmin,ymin = tile_xy(bounds[2],bounds[1],zoom)
    xmax,ymax = tile_xy(bounds[0],bounds[3],zoom)
    return [(x,y) for x in range(xmin,xmax+1) for y in range(ymin,ymax+1)]


def homes_candidates(bounds, layout, freeword):
    warnings, candidates, zoom = [], {}, 15
    tiles = viewport_tiles(bounds, zoom)
    for offset in range(0, len(tiles), 6):
        part = tiles[offset:offset+6]
        data = map_condition(layout, freeword)
        data['zoom'] = zoom
        for i,(x,y) in enumerate(part):
            data[f'tiles[{i}][x]'], data[f'tiles[{i}][y]'] = x,y
        try:
            payload = map_request(MAP_TILE_URL,data).json()
            if not isinstance(payload, dict):
                continue
            for group in payload.values():
                if not isinstance(group, dict):
                    continue
                for row in group.get('row_set',[]):
                    try:
                        if inside(dict(lat=float(row['lat']),lng=float(row['lon'])),bounds):
                            key = row.get('tykey')
                            if key and re.fullmatch(r'[a-zA-Z0-9]+',str(key)):
                                candidates[str(key)] = row
                    except (KeyError, TypeError, ValueError):
                        continue
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
            warnings.append(f'HOME’Sの検索区画の一部を取得できませんでした（{type(error).__name__}）。')
    return candidates, warnings


def verify_homes_detail(url, bounds, region, layout, extra_hints=None):
    html = fetch_html(url)
    p = parse_listing(html, url, datetime.now(timezone.utc).isoformat(timespec='seconds'),
                      bounds=bounds, region=region, extra_hints=extra_hints)
    if not p or (layout not in (None,'ALL') and p.get('layout') != layout) or not target_property(p) or not inside(p,bounds):
        return None
    return p


def collect_homes_region(bounds, layout, region, limit=40, on_record=None, should_stop=None):
    if should_stop and should_stop():
        return [], ['検索を停止しました。']
    if not map_robots_allowed():
        raise ValueError('LIFULL HOME’Sは現在、自動取得を許可していません。')
    warnings, candidates, used_term = [], {}, ''
    for term in region_search_terms(region):
        if should_stop and should_stop():
            return [], warnings + ['検索を停止しました。']
        candidates, local = homes_candidates(bounds, layout, term)
        warnings.extend(local)
        if candidates:
            used_term = term
            break
    if not candidates and not (should_stop and should_stop()):
        candidates, more = homes_candidates(bounds, layout, '')
        warnings.extend(more)
    keys = sorted(candidates, key=lambda k: distance(
        {'lat':float(candidates[k]['lat']),'lng':float(candidates[k]['lon'])}, region))
    keys = keys[:max(1, limit*5)]

    link_hints = {}
    def fetch_building_links(key):
        if should_stop and should_stop():
            return key, candidates[key], [], None
        row = candidates[key]
        data = map_condition(layout, used_term)
        data['cond[tykey]'] = key
        try:
            urls = room_links(map_request(MAP_INFO_URL,data).text)
            return key, row, urls, None
        except (requests.RequestException, ValueError) as error:
            return key, row, [], error
    if keys and not (should_stop and should_stop()):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(HOMES_BUILDING_WORKERS, len(keys)))
        jobs = [pool.submit(fetch_building_links, key) for key in keys]
        try:
            for job in concurrent.futures.as_completed(jobs):
                if should_stop and should_stop():
                    for pending in jobs:
                        pending.cancel()
                    break
                key, row, urls, error = job.result()
                if error is not None:
                    warnings.append(f'HOME’Sの建物候補 {key} を確認できませんでした（{type(error).__name__}）。')
                    continue
                hint = {'lat':float(row['lat']), 'lng':float(row['lon']), 'source':"HOME'S検索地図・建物座標", 'priority':99}
                for url in urls:
                    link_hints.setdefault(url, []).append(hint)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    links = list(link_hints)[:max(1,limit*8)]
    records, failed = [], 0
    def verify_one(url):
        if should_stop and should_stop():
            return None
        return verify_homes_detail(url,bounds,region,layout,link_hints.get(url))
    if links and not (should_stop and should_stop()):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(HOMES_DETAIL_WORKERS, len(links)))
        jobs = [pool.submit(verify_one,u) for u in links]
        try:
            for job in concurrent.futures.as_completed(jobs):
                if should_stop and should_stop():
                    for pending in jobs:
                        pending.cancel()
                    break
                try:
                    p = job.result()
                    if p:
                        records.append(p)
                        if on_record:
                            on_record(p)
                        if len(records) >= limit:
                            for pending in jobs:
                                pending.cancel()
                            break
                    else:
                        failed += 1
                except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
                    failed += 1
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    if failed:
        warnings.append(f'HOME’S「{region["town"]}」{layout}で{failed}件をSRC・築20年以内・地図位置の照合後に除外しました。')
    if should_stop and should_stop():
        warnings.append('検索を停止しました。')
    return deduplicate(records), warnings

def safe_suumo_url(url):
    try:
        p = urlparse(url)
        return (p.scheme == 'https' and p.hostname == 'suumo.jp' and not p.username and not p.password
                and p.port in (None,443) and p.path.startswith('/chintai/'))
    except (ValueError, TypeError):
        return False


def suumo_search_url(region, layout, page=1, term=None):
    params = {'ar':'030','bs':'040','pc':'50','sc':region['muni_code'],
              'fw2':term or region['town'],'page':str(page)}
    code = SUUMO_MD_CODES.get(layout)
    if code and layout not in (None,'ALL'):
        params['md'] = code
    if region.get('pref_code'):
        params['ta'] = region['pref_code']
    return SUUMO_SEARCH_URL + '?' + urlencode(params)


def suumo_fetch_html(url):
    p = urlparse(url)
    if not (p.scheme == 'https' and p.hostname == 'suumo.jp' and p.path == urlparse(SUUMO_SEARCH_URL).path):
        raise ValueError('SUUMOの検索URLが不正です。')
    with _SUUMO_REQUEST_SEMAPHORE:
        r = suumo_session().get(url,headers=SUUMO_HEADERS,timeout=(10,30),allow_redirects=False)
    if 300 <= r.status_code < 400:
        raise ValueError('SUUMOの検索ページが移動しました。')
    r.raise_for_status()
    if len(r.content) > 6_000_000:
        raise ValueError('SUUMOの検索結果が大きすぎます。')
    r.encoding = r.apparent_encoding or 'utf-8'
    return r.text


def suumo_fetch_detail(url):
    if not safe_suumo_url(url):
        raise ValueError('SUUMOの物件URLが不正です。')
    with _SUUMO_REQUEST_SEMAPHORE:
        r = suumo_session().get(url,headers=SUUMO_HEADERS,timeout=(10,30),allow_redirects=False)
    if 300 <= r.status_code < 400:
        raise ValueError('SUUMOの物件ページが移動しました。')
    r.raise_for_status()
    if len(r.content) > 7_000_000:
        raise ValueError('SUUMOの物件ページが大きすぎます。')
    r.encoding = r.apparent_encoding or 'utf-8'
    return r.text


def parse_suumo_money(text):
    value = unicodedata.normalize('NFKC', str(text or '')).strip().replace('－','-')
    if value in ('', '-', '―', 'なし', '無'):
        return 0
    return money(value)


def parse_suumo_list(html, layout, region, limit):
    soup = BeautifulSoup(html,'html.parser')
    rows = []
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for building in soup.select('div.cassetteitem'):
        title = building.select_one('.cassetteitem_content-title')
        address_el = building.select_one('.cassetteitem_detail-col1')
        name = title.get_text(' ',strip=True) if title else 'SUUMO掲載物件'
        address = address_el.get_text(' ',strip=True) if address_el else ''
        if address and not region_matches_address(address, region):
            continue
        list_map_candidates = extract_coordinate_candidates(str(building), building)
        list_hint = max(list_map_candidates, key=lambda x:x['priority']) if list_map_candidates else None
        building_text = building.get_text(' ', strip=True)
        list_age, list_year = building_age_from_values(building_text)
        if list_age is not None and list_age > MAX_BUILDING_AGE:
            continue
        for room in building.select('tr.js-cassette_link'):
            madori = room.select_one('.cassetteitem_madori')
            menseki = room.select_one('.cassetteitem_menseki')
            rent_el = room.select_one('.cassetteitem_price--rent')
            admin_el = room.select_one('.cassetteitem_price--administration')
            link = room.select_one('a.js-cassette_link_href[href]') or room.select_one('a[href*="/chintai/"]')
            room_layout = madori.get_text(strip=True) if madori else ''
            if (layout not in (None,'ALL') and room_layout != layout) or (layout in (None,'ALL') and room_layout not in TARGET_LAYOUTS) or not menseki or not rent_el or not link:
                continue
            url = urljoin('https://suumo.jp',link.get('href','')).split('#')[0]
            if not safe_suumo_url(url):
                continue
            try:
                area_match = re.search(r'(\d+(?:\.\d+)?)',unicodedata.normalize('NFKC',menseki.get_text(strip=True)))
                area = float(area_match.group(1)) if area_match else 0
                rent = parse_suumo_money(rent_el.get_text(strip=True))
                fees = parse_suumo_money(admin_el.get_text(strip=True) if admin_el else '0')
                if not (0 < area <= 1000 and 0 < rent <= 10_000_000 and 0 <= fees <= 10_000_000):
                    continue
            except (ValueError, TypeError, AttributeError):
                continue
            cells = room.select('td')
            floor = cells[2].get_text(' ',strip=True)[:80] if len(cells) > 2 else ''
            row_data=dict(id=url.rstrip('/').split('/')[-1],name=name[:200],address=address[:200],layout=room_layout,
                             rent=rent,fees=fees,area=area,floor=floor,url=url,fetched_at=now,
                             modified='',source='SUUMO',structure='',building_age=(list_age if list_age is not None else ''),
                             built_year=(list_year if list_year is not None else ''),coordinate_source='')
            if list_hint:
                row_data['_map_hints']=[dict(list_hint, source='SUUMO検索結果地図座標', priority=max(97,list_hint.get('priority',0)))]
            rows.append(row_data)
            if len(rows) >= limit:
                return rows
    return rows


def _json_coordinates(value):
    if isinstance(value, list):
        for item in value:
            yield from _json_coordinates(item)
    elif isinstance(value, dict):
        geo = value.get('geo')
        if isinstance(geo, dict):
            try:
                yield float(geo.get('latitude')), float(geo.get('longitude'))
            except (ValueError,TypeError):
                pass
        for v in value.values():
            if isinstance(v,(list,dict)):
                yield from _json_coordinates(v)


def source_coordinates(html, soup, bounds, region, address='', provider='掲載元', extra_hints=None):
    location = resolve_property_location(html, soup, address, bounds, region, provider, extra_hints)
    if not location:
        return None
    return location


def verify_suumo_detail(p, bounds, region):
    html = suumo_fetch_detail(p['url'])
    soup = BeautifulSoup(html,'html.parser')
    structure_text = named_text_value(soup, ('構造','建物構造'))
    structure = normalize_structure(structure_text)
    age_text = named_text_value(soup, ('築年月','築年数'))
    age, year = building_age_from_values(age_text, soup.get_text(' ',strip=True))
    if structure != TARGET_STRUCTURE or age is None or not (0 <= age <= MAX_BUILDING_AGE):
        return None
    location = source_coordinates(html,soup,bounds,region,p.get('address',''),'SUUMO',p.get('_map_hints'))
    if not location:
        return None
    p=dict(p)
    p.pop('_map_hints',None)
    p.update(lat=location['lat'],lng=location['lng'],map_address=location['map_address'],
             address_match=location['address_match'],location_confidence=location['location_confidence'],
             structure='SRC',building_age=age,built_year=year or '',coordinate_source=location['coordinate_source'])
    return p if target_property(p) and inside(p,bounds) else None


def collect_suumo_region(bounds, layout, region, limit=40, on_record=None, should_stop=None):
    if should_stop and should_stop():
        return [], ['検索を停止しました。']
    if not suumo_robots_allowed():
        raise ValueError('SUUMOは現在、自動取得を許可していません。')
    warnings, candidates = [], []
    terms = region_search_terms(region)
    search_terms = terms[:-1] if len(terms)>1 else terms
    if region.get('town_base') and region['town_base'] not in search_terms:
        search_terms.append(region['town_base'])
    seen_urls=set()
    candidate_budget=max(limit*5,100)
    for term in search_terms:
        if should_stop and should_stop():
            return [], warnings + ['検索を停止しました。']
        empty_streak=0
        max_pages=min(20,max(2,math.ceil(candidate_budget/50)+2))
        for page in range(1,max_pages+1):
            if should_stop and should_stop():
                return [], warnings + ['検索を停止しました。']
            try:
                rows=parse_suumo_list(suumo_fetch_html(suumo_search_url(region,layout,page,term)),
                                      layout,region,candidate_budget)
            except (requests.RequestException,ValueError,TypeError,AttributeError) as error:
                warnings.append(f'SUUMO「{region["town"]}」{layout} {page}ページ目を取得できませんでした（{type(error).__name__}）。')
                break
            fresh=[p for p in rows if p['url'] not in seen_urls]
            if not fresh:
                empty_streak += 1
                if empty_streak >= 2:
                    break
            else:
                empty_streak=0
                for p in fresh:
                    seen_urls.add(p['url']); candidates.append(p)
                if len(candidates)>=candidate_budget:
                    break
        if candidates:
            break
    records=[]; rejected=0
    def verify_one(p):
        if should_stop and should_stop():
            return None
        return verify_suumo_detail(p,bounds,region)
    if candidates and not (should_stop and should_stop()):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(SUUMO_DETAIL_WORKERS, len(candidates)))
        jobs=[pool.submit(verify_one,p) for p in candidates]
        try:
            for job in concurrent.futures.as_completed(jobs):
                if should_stop and should_stop():
                    for pending in jobs:
                        pending.cancel()
                    break
                try:
                    p=job.result()
                    if p:
                        records.append(p)
                        if on_record:
                            on_record(p)
                        if len(records)>=limit:
                            for pending in jobs: pending.cancel()
                            break
                    else:
                        rejected += 1
                except (requests.RequestException,ValueError,TypeError,AttributeError,KeyError):
                    rejected += 1
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    if rejected:
        warnings.append(f'SUUMO「{region["town"]}」{layout}で{rejected}件をSRC・築20年以内・地図位置の多段確認後に除外しました。')
    if should_stop and should_stop():
        warnings.append('検索を停止しました。')
    return deduplicate(records),warnings

def collect_visible_regions(bounds, limit=200, progress=None, save_chunk=None,
                            status_hook=None, should_stop=None):
    bounds = normalize_bounds(bounds)
    if not bounds:
        raise ValueError('地図の表示範囲を確認できません。')
    if not (34.8 <= bounds[0] < bounds[2] <= 36.5 and 138.5 <= bounds[1] < bounds[3] <= 140.9):
        raise ValueError('首都圏の範囲で取得してください。')
    if should_stop and should_stop():
        return [], {'layout':'ALL','bounds':bounds,'regions':[],'received':0,'warnings':['検索を停止しました。']}
    regions = visible_regions(bounds, should_stop=should_stop)
    task_specs=[]
    for region in regions:
        task_specs.append((region,"LIFULL HOME'S",collect_homes_region))
        task_specs.append((region,'SUUMO',collect_suumo_region))
    total_steps=len(task_specs)
    warnings=[]; all_records=[]; finished_count=0
    result_lock=threading.Lock()

    def run_task(spec):
        region,source_name,collector=spec
        if should_stop and should_stop():
            return spec,[],['検索を停止しました。']
        def persist_one(record):
            if should_stop and should_stop():
                return
            if save_chunk:
                save_chunk([record])
        try:
            records,local_warnings=collector(bounds,'ALL',region,limit,on_record=persist_one,should_stop=should_stop)
            records=[p for p in records if target_property(p)]
        except (requests.RequestException,ValueError,KeyError,TypeError) as error:
            records=[]; local_warnings=[str(error)]
        return spec,records,local_warnings

    if progress:
        progress(0,total_steps,f'全間取りをまとめて検索：{len(regions)}地域 × 2取得元')
    if task_specs and not (should_stop and should_stop()):
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(REGION_TASK_WORKERS,len(task_specs))) as pool:
            jobs=[pool.submit(run_task,spec) for spec in task_specs]
            for job in concurrent.futures.as_completed(jobs):
                if should_stop and should_stop():
                    for pending in jobs:
                        pending.cancel()
                    break
                spec,records,local_warnings=job.result()
                region,source_name,_=spec
                with result_lock:
                    warnings.extend(local_warnings)
                    all_records.extend(records)
                    finished_count += 1
                    current_finished=finished_count
                if progress:
                    progress(current_finished,total_steps,f'{region["label"]}｜{source_name} 完了')
                if status_hook:
                    status_hook(current_finished,total_steps,region,source_name,warnings)
    combined=deduplicate(all_records)
    info=dict(layout='ALL',layouts=list(TARGET_LAYOUTS),layout_groups={k:list(v) for k,v in LAYOUT_GROUPS.items()},
              bounds=bounds,regions=[r['label'] for r in regions],per_region_source_limit=limit,
              received=len(combined),
              source_counts={name:sum(p.get('source')==name for p in combined) for name in ("LIFULL HOME'S",'SUUMO')},
              raw_layout_counts={name:sum(p.get('layout')==name for p in combined) for name in TARGET_LAYOUTS},
              layout_counts={group:sum(layout_matches_group(p.get('layout'),group) for p in combined) for group in LAYOUTS},
              filters={'structure':'SRC','max_age':20,'walk_limit':None},warnings=warnings[-100:],
              parallel={'region_tasks':REGION_TASK_WORKERS,'homes_buildings':HOMES_BUILDING_WORKERS,
                        'homes_details':HOMES_DETAIL_WORKERS,'suumo_details':SUUMO_DETAIL_WORKERS},
              source_url="LIFULL HOME'S + SUUMO",fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return combined,info


def background_job_id(bounds, limit):
    raw=json.dumps([BACKGROUND_ENGINE_VERSION,list(map(lambda x:round(x,5),bounds)),int(limit)],ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _parse_iso(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError,AttributeError):
        return None


def _job_is_fresh(job, seconds=WORKER_STALE_SECONDS):
    dt=_parse_iso(job.get('heartbeat_at') or job.get('updated_at')) if job else None
    return bool(dt and (datetime.now(timezone.utc)-dt).total_seconds() < seconds)


class IncrementalSaver:
    def __init__(self, job_id, settings, batch_size=8, flush_seconds=4):
        self.job_id=job_id; self.settings=settings
        self.batch_size=batch_size; self.flush_seconds=flush_seconds
        self.lock=threading.Lock(); self.buffer={}; self.last_flush=time.monotonic()
        self.saved_ids=set(); self.save_errors=0; self.attempted=0

    def add(self, records):
        batch=None
        with self.lock:
            for p in records:
                identity=p.get('url') or str(p.get('id',''))
                if identity:
                    self.buffer[identity]=p
                    self.attempted += 1
            if len(self.buffer) >= self.batch_size or time.monotonic()-self.last_flush >= self.flush_seconds:
                batch=list(self.buffer.values()); self.buffer.clear(); self.last_flush=time.monotonic()
        if batch:
            self._save(batch)

    def flush(self):
        with self.lock:
            batch=list(self.buffer.values()); self.buffer.clear(); self.last_flush=time.monotonic()
        if batch:
            self._save(batch)

    def _save(self,batch):
        try:
            ids=save_incremental_records(batch,settings=self.settings)
            self.saved_ids.update(ids)
        except StorageError:
            self.save_errors += len(batch)
            raise


def worker_should_stop(job_id, settings, state):
    now=time.monotonic()
    if now-state.get('last_poll',0) < WORKER_HEARTBEAT_SECONDS:
        return state.get('cancelled',False)
    state['last_poll']=now
    try:
        job=job_get(job_id,settings)
        state['cancelled']=bool(job.get('cancel_requested'))
    except StorageError:
        pass
    return state.get('cancelled',False)


def background_worker_process(job_id,bounds,limit,settings):
    try:
        if hasattr(os,'nice'):
            try: os.nice(10)
            except OSError: pass
        ensure_schema_v05(settings)
        job_upsert(job_id,{
            'state':'running','bounds':list(bounds),'limit_per_source':int(limit),
            'total_tasks':0,'completed_tasks':0,'save_attempted':0,'saved_count':0,
            'save_errors':0,'cancel_requested':False,'heartbeat_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'message':'表示地域を確認しています…','error':'','payload':{'engine_version':BACKGROUND_ENGINE_VERSION}},settings)
        saver=IncrementalSaver(job_id,settings)
        stop_state={'last_poll':0.0,'cancelled':False}
        status={'step':0,'total':0,'last_push':0.0}
        def should_stop():
            return worker_should_stop(job_id,settings,stop_state)
        def push(force=False,message=None):
            now=time.monotonic()
            if not force and now-status['last_push'] < 4:
                return
            status['last_push']=now
            job_upsert(job_id,{
                'state':'running','bounds':list(bounds),'limit_per_source':int(limit),
                'total_tasks':int(status['total'] or 0),'completed_tasks':int(status['step'] or 0),
                'save_attempted':int(saver.attempted),'saved_count':len(saver.saved_ids),
                'save_errors':int(saver.save_errors),'cancel_requested':bool(stop_state.get('cancelled')),
                'heartbeat_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'message':message or '検索中','error':'',
                'payload':{'engine_version':BACKGROUND_ENGINE_VERSION}},settings)
        def progress(step,total,message):
            status['step']=step; status['total']=total; push(False,message)
        def status_hook(step,total,region,source_name,warnings):
            status['step']=step; status['total']=total; push(False,f'{region["label"]}｜{source_name} 完了')
        records,info=collect_visible_regions(bounds,limit,progress=progress,save_chunk=saver.add,
                                             status_hook=status_hook,should_stop=should_stop)
        saver.flush()
        if should_stop():
            job_upsert(job_id,{
                'state':'cancelled','bounds':list(bounds),'limit_per_source':int(limit),
                'total_tasks':int(status['total'] or 0),'completed_tasks':int(status['step'] or 0),
                'save_attempted':int(saver.attempted),'saved_count':len(saver.saved_ids),
                'save_errors':int(saver.save_errors),'cancel_requested':True,
                'heartbeat_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'message':'検索を停止しました。','error':'','payload':{'info':info,'engine_version':BACKGROUND_ENGINE_VERSION}},settings)
            return
        save_metadata('last_fetch',info,settings)
        _,_,namespace=settings
        _upsert_rows('sumai_fetch_queries',[{'namespace':namespace,'identity':query_key(bounds,'ALL',limit),'payload':info}],
                     'namespace,identity',settings)
        job_upsert(job_id,{
            'state':'completed','bounds':list(bounds),'limit_per_source':int(limit),
            'total_tasks':int(status['total'] or 0),'completed_tasks':int(status['total'] or status['step'] or 0),
            'save_attempted':int(saver.attempted),'saved_count':len(saver.saved_ids),
            'save_errors':int(saver.save_errors),'cancel_requested':False,
            'heartbeat_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'message':f'完了：{len(saver.saved_ids)}件を保存','error':'',
            'payload':{'info':info,'engine_version':BACKGROUND_ENGINE_VERSION}},settings)
    except Exception as error:
        try:
            job_upsert(job_id,{
                'state':'failed','bounds':list(bounds),'limit_per_source':int(limit),
                'heartbeat_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'message':'検索処理が停止しました。','error':f'{type(error).__name__}: {error}',
                'payload':{'engine_version':BACKGROUND_ENGINE_VERSION}},settings)
        except Exception:
            pass


def launch_background_process(bounds,limit,settings):
    bounds=normalize_bounds(bounds)
    if not bounds:
        raise ValueError('地図の表示範囲を確認できません。')
    ensure_schema_v05(settings)
    job_id=background_job_id(bounds,limit)
    existing=job_get(job_id,settings)
    if existing.get('state') in ('queued','running') and _job_is_fresh(existing):
        return job_id
    job_upsert(job_id,{
        'state':'queued','bounds':list(bounds),'limit_per_source':int(limit),
        'total_tasks':0,'completed_tasks':0,'save_attempted':0,'saved_count':0,
        'save_errors':0,'cancel_requested':False,
        'heartbeat_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'message':'検索ワーカーを起動しています…','error':'',
        'payload':{'engine_version':BACKGROUND_ENGINE_VERSION}},settings)
    raw=json.dumps({'job_id':job_id,'bounds':list(bounds),'limit':int(limit)},ensure_ascii=False).encode('utf-8')
    token=base64.urlsafe_b64encode(raw).decode('ascii')
    env=os.environ.copy()
    env['SUPABASE_URL']=secret_value('SUPABASE_URL')
    env['SUPABASE_SECRET_KEY']=secret_value('SUPABASE_SECRET_KEY') or secret_value('SUPABASE_SERVICE_ROLE_KEY')
    env['SUPABASE_NAMESPACE']=secret_value('SUPABASE_NAMESPACE','sumai-compass')
    env['PYTHONUNBUFFERED']='1'
    try:
        subprocess.Popen([sys.executable,os.path.abspath(__file__),'--sumai-worker',token],
                         cwd=os.path.dirname(os.path.abspath(__file__)),env=env,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         start_new_session=True,close_fds=True)
    except OSError as error:
        job_upsert(job_id,{'state':'failed','bounds':list(bounds),'limit_per_source':int(limit),
                           'message':'検索ワーカーを起動できませんでした。','error':str(error),
                           'heartbeat_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                           'payload':{'engine_version':BACKGROUND_ENGINE_VERSION}},settings)
        raise StorageError('バックグラウンド検索プロセスを起動できませんでした。') from None
    return job_id


def worker_cli(token):
    data=json.loads(base64.urlsafe_b64decode(token.encode('ascii')).decode('utf-8'))
    settings=supabase_settings()
    background_worker_process(data['job_id'],data['bounds'],int(data['limit']),settings)


def main():
    st.set_page_config(page_title='住まいコンパス', page_icon='🏠', layout='wide', initial_sidebar_state='collapsed')
    st.markdown('''<style>
    .stApp{background:white;color:#173a5e;color-scheme:light}
    .block-container{padding-top:1rem;padding-bottom:2rem}
    h1,h2,h3,p,label{color:#173a5e}
    [data-testid="stMetric"]{background:#f1f5f9;padding:8px;border-radius:10px}
    @media(max-width:760px){.block-container{padding-left:.7rem;padding-right:.7rem}h1{font-size:1.5rem}}
    </style>''', unsafe_allow_html=True)
    st.title('住まいコンパス')
    st.caption('掲載物件の総額家賃を、街の中で比較')
    state=st.session_state
    state.setdefault('records02',[]); state.setdefault('info02',{}); state.setdefault('map_enabled02',False)
    state.setdefault('background_job_id',None); state.setdefault('facilities02',[]); state.setdefault('facility_origin02',None)

    if not state.map_enabled02:
        st.caption('起動時はSupabase、物件データ、地図コンポーネント、検索ワーカーへアクセスしません。')
        if st.button('地図と検索画面を開く',type='primary',key='enable_map02'):
            state.map_enabled02=True; st.rerun()
        return

    layout=st.radio('間取り区分',LAYOUTS,index=1,horizontal=True,key='layout02')
    with st.expander('地域・色分け・物件取得の設定',expanded=False):
        station_name=st.selectbox('地図の中心駅',list(BY_NAME),index=list(BY_NAME).index('池袋'),key='center02')
        c1,c2=st.columns(2)
        cell=c1.selectbox('メッシュの大きさ',[100,200,300],format_func=lambda x:f'{x}m',key='cell02')
        radius=c2.selectbox('駅からの初期表示範囲',[1000,1500,2000,3000],index=1,format_func=lambda x:f'{x/1000:g}km',key='radius02')
        c1,c2=st.columns(2)
        interpolate=c1.checkbox('近隣データから未掲載メッシュも推定',value=True,key='interpolate02')
        reach=c2.slider('推定に使う建物の範囲（m）',200,700,400,50,key='reach02')
        budget=st.number_input('物件一覧・ピンの月額上限（万円）',1.0,1000.0,50.0,1.0,key='budget02')
        limit=st.selectbox('1地域・1取得元あたりの確認上限',[100,200,400,800],index=1,key='limit02')
        st.caption('全間取りを1回の地域検索でまとめて取得します。対象はSRC・築20年以内、駅徒歩は無制限です。')

    origin=BY_NAME[station_name]
    if state.get('map_station03')!=station_name:
        state.map_station03=station_name; state.bounds03=None; state.view_center03=[origin['lat'],origin['lng']]; state.zoom03=15
    bounds=state.get('bounds03')
    view=dict(lat=state.view_center03[0],lng=state.view_center03[1],name=station_name)
    render_radius=bounds_radius(bounds,view) if bounds else radius
    all_layout=[p for p in state.records02 if layout_matches_group(p.get('layout'),layout) and target_property(p)]
    nearby=[p for p in all_layout if inside(p,bounds)] if bounds else all_layout
    support=[p for p in all_layout if distance(p,view)*1000<=render_radius+reach] if bounds else []
    displayed=[p for p in nearby if p['rent']+p['fees']<=budget*10000]
    cells=mesh(support,view,cell,render_radius,interpolate,reach) if bounds and support and render_radius<=12000 else []
    if bounds: cells=[c for c in cells if cell_intersects(c,bounds)]

    def remember_view():
        data=state.get(f'map03_{station_name}',{})
        if isinstance(data,dict):
            valid=normalize_bounds(data.get('bounds'))
            if valid:
                state.bounds03=valid; state.view_center03=[(valid[0]+valid[2])/2,(valid[1]+valid[3])/2]
                zoom=data.get('zoom')
                if isinstance(zoom,(int,float)) and 1<=zoom<=20: state.zoom03=int(zoom)

    from streamlit_folium import st_folium
    st_folium(make_map(view,cells,displayed,state.facilities02),height=560,use_container_width=True,
              key=f'map03_{station_name}',returned_objects=['bounds','zoom'],
              center=tuple(state.view_center03),zoom=state.zoom03,on_change=remember_view)

    st.caption('地図を移動・拡大して表示範囲を決めます。保存済みデータは現在の表示範囲だけSupabaseから取得します。')
    c1,c2=st.columns(2)
    if c1.button('表示範囲の保存済み物件を読み込む',disabled=bounds is None,key='load_visible02'):
        try:
            settings=supabase_settings(); ensure_schema_v05(settings)
            records=load_viewport_store(bounds,layouts=TARGET_LAYOUTS,settings=settings,limit=3000,padding_m=reach)
            state.records02=records; state.info02={'source_url':'Supabase表示範囲','fetched_at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
            st.rerun()
        except StorageError as error: st.error(str(error))
    if c2.button('表示範囲をバックグラウンド取得・保存',type='primary',disabled=bounds is None,key='fetch03'):
        try:
            settings=supabase_settings(); ensure_schema_v05(settings)
            jid=launch_background_process(bounds,limit,settings)
            state.background_job_id=jid; st.success('バックグラウンド検索を開始しました。画面を閉じても同じサーバーが稼働している間は継続します。')
        except (StorageError,ValueError,TypeError) as error: st.error(str(error))

    st.divider()
    jid=state.get('background_job_id')
    if not jid and st.button('直近の検索状況を確認',key='latest_job02'):
        try:
            job=latest_job(); state.background_job_id=job.get('job_id') if job else None; st.rerun()
        except StorageError as error: st.error(str(error))
    jid=state.get('background_job_id')
    if jid:
        try:
            job=job_get(jid)
        except StorageError as error:
            job={}; st.error(str(error))
        if job:
            state_name=job.get('state','')
            step=int(job.get('completed_tasks',0) or 0); total=max(1,int(job.get('total_tasks',0) or 1))
            st.progress(min(1.0,step/total),text=f'{state_name}｜処理 {step}/{total}｜保存 {int(job.get("saved_count",0) or 0)}件')
            st.caption(job.get('message',''))
            c1,c2,c3=st.columns(3)
            if c1.button('進捗を更新',key='refresh_job02'): st.rerun()
            if c2.button('検索を停止',disabled=state_name not in ('queued','running'),key='cancel_job02'):
                try: job_cancel(jid); st.rerun()
                except StorageError as error: st.error(str(error))
            if c3.button('保存済みを地図へ反映',disabled=bounds is None,key='apply_saved02'):
                try:
                    records=load_viewport_store(bounds,layouts=TARGET_LAYOUTS,limit=3000,padding_m=reach)
                    state.records02=records
                    info=job.get('payload',{}).get('info',{}) if isinstance(job.get('payload'),dict) else {}
                    state.info02=info; st.rerun()
                except StorageError as error: st.error(str(error))
            if state_name=='failed': st.error(job.get('error','検索処理が停止しました。'))

    labels=['15万円以下','15〜20万円','20〜22.5万円','22.5〜25万円','25〜27.5万円','27.5〜30万円','30〜35万円','35万円超']
    st.markdown('<div style="display:flex;flex-wrap:wrap;gap:10px;margin:6px 0">'+''.join(
        f'<span style="font-size:13px;color:#173a5e"><i style="display:inline-block;width:13px;height:13px;background:{color};margin-right:4px"></i>{label}</span>'
        for color,label in zip(COLORS,labels))+'</div>',unsafe_allow_html=True)
    c1,c2,c3=st.columns(3)
    c1.metric('範囲内の募集住戸',f'{len(nearby)}件')
    c2.metric('掲載建物の位置数',f'{len({(round(p["lat"],5),round(p["lng"],5)) for p in nearby})}箇所')
    c3.metric('色分けメッシュ',f'{len(cells)}区画')

    if displayed:
        with st.expander('地図内の物件一覧',expanded=False):
            st.dataframe([{'物件名':p['name'],'間取り':p['layout'],'総額（万円）':(p['rent']+p['fees'])/10000,
                           '面積（㎡）':p['area'],'築年数':p.get('building_age',''),'掲載住所':p.get('address',''),
                           '地図判定住所':p.get('map_address',''),'位置精度':p.get('location_confidence',''),
                           '座標根拠':p.get('coordinate_source',''),'募集ページ':p.get('url','')}
                          for p in sorted(displayed,key=lambda x:x['rent']+x['fees'])],
                         hide_index=True,width='stretch',column_config={'募集ページ':st.column_config.LinkColumn('募集ページ')})

    with st.expander('Supabase・バックアップ'):
        st.caption('通常表示では全件読み込みを行いません。全件CSVバックアップだけ明示操作で実行します。')
        if st.button('全件CSVバックアップを準備',key='backup_all02'):
            try:
                records,info=load_all_store(maximum=50000); state.backup_records02=records; st.success(f'{len(records)}件を準備しました。')
            except StorageError as error: st.error(str(error))
        if state.get('backup_records02') is not None:
            st.download_button('CSVをダウンロード',export_records(state.backup_records02),'sumai_geo_properties.csv','text/csv')
        upload=st.file_uploader('座標付き物件CSV',type=['csv'])
        if st.button('CSVを追加保存',disabled=upload is None,key='csv_save02'):
            try:
                imported=validate_records(upload.getvalue())
                info=dict(source_url='CSV',fetched_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),warnings=[])
                save_store(imported,info); st.success(f'{len(imported)}件を保存しました。')
            except (ValueError,UnicodeError,csv.Error,StorageError,OSError) as error: st.error(str(error))
        st.caption('v05ではlat/lng/間取り/構造/築年数を通常列にも保存し、現在の表示範囲だけをインデックス検索します。')

    with st.expander('集計・取得方法'):
        st.write('検索対象はSRC・築20年以内。駅徒歩は絞りません。全対象間取りを地域ごとにまとめて検索し、HOME’SとSUUMOを確認します。')
        st.write('物件位置は掲載地図・埋込座標・検索地図座標を優先し、国土地理院の逆ジオコーディングで町名・丁目との整合を確認します。')
        st.write('バックグラウンド検索はStreamlit本体とは別Pythonプロセスで実行し、UIプロセス内に長時間ThreadPoolを残しません。')


if __name__ == '__main__':
    if '--sumai-worker' in sys.argv:
        idx=sys.argv.index('--sumai-worker')
        if idx+1 >= len(sys.argv):
            raise SystemExit(2)
        worker_cli(sys.argv[idx+1])
    else:
        main()
