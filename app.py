"""住まいコンパス — Streamlit版 v01
実行: streamlit run app.py
依存: streamlit>=1.50,<2 / folium>=0.18,<1 / streamlit-folium>=0.24,<1 / requests>=2.32,<3
家賃サンプル・通勤概算の試作版。物件データはセッション内＋CSVバックアップ。
"""
from __future__ import annotations
import csv
import heapq
import io
import math
import uuid
from html import escape
from urllib.parse import urlencode, urlparse

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

STATIONS = [{'name': '東京', 'lat': 35.6812, 'lng': 139.7671, 'region': '東京', 'base': 16},
 {'name': '品川', 'lat': 35.6285, 'lng': 139.7388, 'region': '東京', 'base': 17},
 {'name': '大崎', 'lat': 35.6197, 'lng': 139.7286, 'region': '東京', 'base': 16},
 {'name': '五反田', 'lat': 35.6264, 'lng': 139.7235, 'region': '東京', 'base': 16},
 {'name': '目黒', 'lat': 35.6339, 'lng': 139.7158, 'region': '東京', 'base': 18},
 {'name': '恵比寿', 'lat': 35.6467, 'lng': 139.7101, 'region': '東京', 'base': 20},
 {'name': '渋谷', 'lat': 35.658, 'lng': 139.7016, 'region': '東京', 'base': 20},
 {'name': '新宿', 'lat': 35.6909, 'lng': 139.7003, 'region': '東京', 'base': 17},
 {'name': '池袋', 'lat': 35.7295, 'lng': 139.7109, 'region': '東京', 'base': 14},
 {'name': '上野', 'lat': 35.7138, 'lng': 139.777, 'region': '東京', 'base': 14},
 {'name': '秋葉原', 'lat': 35.6984, 'lng': 139.7731, 'region': '東京', 'base': 15},
 {'name': '赤羽', 'lat': 35.778, 'lng': 139.7209, 'region': '東京', 'base': 11},
 {'name': '川口', 'lat': 35.8019, 'lng': 139.7175, 'region': '埼玉', 'base': 9},
 {'name': '浦和', 'lat': 35.8585, 'lng': 139.6571, 'region': '埼玉', 'base': 10},
 {'name': '大宮', 'lat': 35.9063, 'lng': 139.6238, 'region': '埼玉', 'base': 9},
 {'name': '大井町', 'lat': 35.6063, 'lng': 139.7346, 'region': '東京', 'base': 14},
 {'name': '蒲田', 'lat': 35.5625, 'lng': 139.716, 'region': '東京', 'base': 11},
 {'name': '川崎', 'lat': 35.5313, 'lng': 139.697, 'region': '神奈川', 'base': 11},
 {'name': '横浜', 'lat': 35.4662, 'lng': 139.622, 'region': '神奈川', 'base': 12},
 {'name': '武蔵小杉', 'lat': 35.575, 'lng': 139.6595, 'region': '神奈川', 'base': 13},
 {'name': '日吉', 'lat': 35.553, 'lng': 139.6468, 'region': '神奈川', 'base': 11},
 {'name': '自由が丘', 'lat': 35.6074, 'lng': 139.6685, 'region': '東京', 'base': 16},
 {'name': '中目黒', 'lat': 35.6443, 'lng': 139.699, 'region': '東京', 'base': 19},
 {'name': '中野', 'lat': 35.7058, 'lng': 139.6658, 'region': '東京', 'base': 13},
 {'name': '荻窪', 'lat': 35.7045, 'lng': 139.6201, 'region': '東京', 'base': 12},
 {'name': '吉祥寺', 'lat': 35.7032, 'lng': 139.5797, 'region': '東京', 'base': 14},
 {'name': '三鷹', 'lat': 35.7027, 'lng': 139.5603, 'region': '東京', 'base': 12},
 {'name': '国分寺', 'lat': 35.7001, 'lng': 139.4808, 'region': '東京', 'base': 10},
 {'name': '立川', 'lat': 35.6982, 'lng': 139.4137, 'region': '東京', 'base': 10},
 {'name': '錦糸町', 'lat': 35.696, 'lng': 139.814, 'region': '東京', 'base': 12},
 {'name': '新小岩', 'lat': 35.7169, 'lng': 139.8586, 'region': '東京', 'base': 10},
 {'name': '市川', 'lat': 35.7289, 'lng': 139.9084, 'region': '千葉', 'base': 10},
 {'name': '船橋', 'lat': 35.7017, 'lng': 139.985, 'region': '千葉', 'base': 9},
 {'name': '津田沼', 'lat': 35.6907, 'lng': 140.0207, 'region': '千葉', 'base': 9},
 {'name': '千葉', 'lat': 35.6134, 'lng': 140.1133, 'region': '千葉', 'base': 8},
 {'name': '北千住', 'lat': 35.7494, 'lng': 139.805, 'region': '東京', 'base': 11},
 {'name': '松戸', 'lat': 35.7847, 'lng': 139.9007, 'region': '千葉', 'base': 8},
 {'name': '柏', 'lat': 35.8622, 'lng': 139.9711, 'region': '千葉', 'base': 8},
 {'name': '二子玉川', 'lat': 35.6117, 'lng': 139.6267, 'region': '東京', 'base': 16},
 {'name': '溝の口', 'lat': 35.5998, 'lng': 139.6115, 'region': '神奈川', 'base': 11},
 {'name': '南浦和', 'lat': 35.8476, 'lng': 139.669, 'region': '埼玉', 'base': 9}]
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
LAYOUTS = ('1LDK', '2LDK', '3LDK', '4LDK')
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


@st.cache_data(show_spinner=False)
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


def rent_band(station, layout, properties):
    added = [p for p in properties if p['station'] == station['name'] and p['layout'] == layout]
    if added:
        amounts = [p['rent']+p['management']+p['common'] for p in added]
        return min(amounts)/10000, max(amounts)/10000, added
    factor = {'1LDK': 1, '2LDK': 1.45, '3LDK': 1.85, '4LDK': 2.35}[layout]
    return round(station['base']*factor*.8, 1), round(station['base']*factor*1.2, 1), []


def calculate(station, layout, properties, destination, home_walk, work_walk):
    r = route(station['name'], destination)
    low, high, added = rent_band(station, layout, properties)
    return dict(station, route=r, low=low, high=high, properties=added,
                door=math.ceil(r['time']+home_walk+work_walk+(3 if r['legs'] else 0)),
                distance=r['km']+(home_walk+work_walk)*.08)


def safe_url(value):
    parsed = urlparse(str(value))
    return parsed.scheme in ('https', 'http') and bool(parsed.netloc)


def parse_properties(raw):
    """Validate the whole file before mutation. Import is all-or-nothing."""
    if len(raw) > 2_000_000:
        raise ValueError('CSVは2MB以内にしてください。')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or not set(CSV_FIELDS)-{'id', 'url'} <= set(reader.fieldnames):
        raise ValueError('このアプリで書き出したCSVを選択してください。')
    result = []
    seen = set()
    for number, row in enumerate(reader, 2):
        if len(result) >= 1000:
            raise ValueError('物件は1,000件以内にしてください。')
        if row.get('station') not in BY_NAME or row.get('layout') not in LAYOUTS:
            raise ValueError(f'{number}行目：駅または間取りが不正です。')
        name = row.get('name', '').strip()
        if name.startswith("'") and name[1:].startswith(('=', '+', '-', '@', '\t', '\r')):
            name = name[1:]
        if not name or len(name) > 60:
            raise ValueError(f'{number}行目：物件名は1〜60文字で指定してください。')
        money = {}
        for field in ('rent', 'management', 'common'):
            try:
                value = int(row.get(field, ''))
            except (ValueError, TypeError):
                raise ValueError(f'{number}行目：金額は整数で入力してください。') from None
            if value < (1 if field == 'rent' else 0) or value > 10_000_000:
                raise ValueError(f'{number}行目：金額が入力範囲外です。')
            money[field] = value
        url = row.get('url', '').strip()
        if url and not safe_url(url):
            raise ValueError(f'{number}行目：URLはhttpまたはhttpsで指定してください。')
        ident = row.get('id', '').strip() or str(uuid.uuid4())
        if ident.startswith("'") and ident[1:].startswith(('=', '+', '-', '@', '\t', '\r')):
            ident = ident[1:]
        if len(ident) > 100 or ident in seen:
            raise ValueError(f'{number}行目：物件IDが重複または不正です。')
        seen.add(ident)
        result.append(dict(id=ident, name=name, station=row['station'], layout=row['layout'], url=url, **money))
    return result


def export_properties(properties):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for p in properties:
        row = dict(p)
        # Avoid spreadsheet formula execution. Imports reverse this protective prefix.
        for field in ('name', 'url', 'id'):
            value = str(row.get(field, ''))
            if value.startswith(('=', '+', '-', '@', '\t', '\r')):
                row[field] = "'"+value
        writer.writerow({key: row.get(key, '') for key in CSV_FIELDS})
    return output.getvalue().encode('utf-8-sig')


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_facilities(station_name, category):
    s = BY_NAME[station_name]
    query = (f'[out:json][timeout:15];nwr{FACILITY_FILTERS[category]}'
             f'(around:1000,{s["lat"]},{s["lng"]});out center tags;')
    response = requests.post('https://overpass-api.de/api/interpreter', data={'data': query},
                             timeout=(5, 20), headers={'User-Agent': 'SumaiCompass/1.0'})
    response.raise_for_status()
    data = response.json()
    facilities = []
    for element in data.get('elements', []):
        center = element.get('center', {})
        lat, lng = element.get('lat', center.get('lat')), element.get('lon', center.get('lon'))
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        tags = element.get('tags', {})
        facility = dict(name=tags.get('name') or tags.get('brand') or '名称未登録', lat=lat, lng=lng)
        facility['meters'] = round(distance(s, facility)*1000)
        facilities.append(facility)
    return sorted(facilities, key=lambda f: f['meters'])[:60]


def build_map(rows, selected, destination, facilities, focus):
    center = [BY_NAME[selected]['lat'], BY_NAME[selected]['lng']] if selected else [35.69, 139.73]
    m = folium.Map(location=center, zoom_start=12 if focus else 10, control_scale=True, tiles='OpenStreetMap')
    for row in rows:
        name = row['name']
        color = '#173a5e' if name == selected else ('#ad5a25' if row['properties'] else '#196f9b')
        source = '入力物件' if row['properties'] else '架空サンプル'
        html = (f'<div style="background:white;border:2px solid {color};border-radius:9px;'
                'padding:6px 8px;white-space:nowrap;box-shadow:0 2px 8px #0002;text-align:center;'
                f'color:{color};font: bold 13px sans-serif">{row["low"]:g}〜{row["high"]:g}万'
                f'<br><span style="font-size:11px;font-weight:normal">{escape(name)} · {source}</span></div>')
        folium.Marker([row['lat'], row['lng']], tooltip=name,
                      icon=folium.DivIcon(html=html, icon_size=(150, 48), icon_anchor=(75, 24)),
                      popup=folium.Popup(f'{escape(name)} · {source}<br>約{row["door"]}分 / 乗り換え{row["route"]["transfers"]}回', max_width=240)).add_to(m)
    if selected:
        r = route(selected, destination)
        if len(r['path']) > 1:
            folium.PolyLine([[BY_NAME[n]['lat'], BY_NAME[n]['lng']] for n in r['path']],
                            color='#196f9b', weight=4, dash_array='7,5', tooltip='概算経路・駅を結ぶ参考線').add_to(m)
    for f in facilities:
        folium.CircleMarker([f['lat'], f['lng']], radius=7, color='white', weight=2,
                            fill=True, fill_color='#ad5a25', fill_opacity=1,
                            tooltip=f['name'], popup=escape(f['name'])).add_to(m)
    if rows and not focus:
        m.fit_bounds([[r['lat'], r['lng']] for r in rows], padding=(40, 40), max_zoom=12)
    return m


def main():
    st.set_page_config(page_title='住まいコンパス', page_icon='🏠', layout='wide', initial_sidebar_state='collapsed')
    st.markdown('''<style>
    .stApp {background:#fff;color:#173a5e;color-scheme:light}
    h1,h2,h3,p,label {color:#173a5e}
    .block-container {padding-top:1.2rem;padding-bottom:2rem}
    [data-testid="stSidebar"] {background:#f1f5f9}
    [data-testid="stMetric"] {background:#f1f5f9;border-radius:10px;padding:10px}
    @media(max-width:760px){.block-container{padding-left:.8rem;padding-right:.8rem}h1{font-size:1.6rem}}
    </style>''', unsafe_allow_html=True)
    st.title('住まいコンパス')
    st.caption('東京・神奈川・埼玉・千葉｜主要41駅')
    state = st.session_state
    if 'flash' in state:
        st.success(state.pop('flash'))
    if 'properties' not in state:
        state.properties = []
    if 'selected' not in state:
        state.selected = '武蔵小杉'
    if 'facilities' not in state:
        state.facilities = []
        state.facility_key = None
    with st.expander('検索・通勤条件', expanded=True):
        layout = st.radio('間取り', LAYOUTS, index=1, horizontal=True, key='layout')
        c1, c2 = st.columns(2)
        budget = c1.number_input('月額上限（万円・管理費・共益費込み）', 1.0, 3000.0, 30.0, 1.0, key='budget')
        region = c2.selectbox('エリア', ['首都圏全体', '東京', '神奈川', '埼玉', '千葉'], key='region')
        destination = st.selectbox('勤務先の最寄り駅', list(BY_NAME), key='destination')
        c1, c2 = st.columns(2)
        home_walk = c1.number_input('自宅 → 駅（徒歩・分）', 0, 60, 10, key='home_walk')
        work_walk = c2.number_input('駅 → 勤務先（徒歩・分）', 0, 60, 8, key='work_walk')
        c1, c2 = st.columns(2)
        max_time = c1.selectbox('通勤時間上限', ['指定なし', 30, 45, 60, 90], key='max_time')
        max_transfers = c2.selectbox('乗り換え上限', ['指定なし', 0, 1, 2], key='max_transfers')
        order = st.selectbox('並び順', ['家賃が低い順', '通勤が短い順', '乗り換えが少ない順'], key='order')
        only_real = st.checkbox('入力した物件があるエリアだけ表示', key='only_real')
    st.warning('初期家賃帯は架空のサンプルです。市場相場・募集中の物件ではありません。通勤時間と距離は概算です。')
    rows = [calculate(s, layout, state.properties, destination, home_walk, work_walk) for s in STATIONS]
    rows = [r for r in rows if (region == '首都圏全体' or r['region'] == region)
            and r['low'] <= budget and (max_time == '指定なし' or r['door'] <= max_time)
            and (max_transfers == '指定なし' or r['route']['transfers'] <= max_transfers)
            and (not only_real or r['properties'])]
    rows.sort(key=lambda r: (r['door'], r['low']) if order == '通勤が短い順' else
              (r['route']['transfers'], r['door']) if order == '乗り換えが少ない順' else (r['low'], r['door']))
    if rows:
        names = [r['name'] for r in rows]
        if state.selected not in names:
            state.selected = names[0]
        selected = st.selectbox('地図に表示する候補の詳細', names, key='selected')
    else:
        selected = None
        st.info('条件に合う候補がありません。予算や通勤条件を変更してください。')
    c1, c2 = st.columns(2)
    category = c1.selectbox('周辺施設の種類', list(FACILITY_FILTERS), key='category')
    focus = c2.checkbox('選択した駅に地図を合わせる', value=False, key='focus')
    facility_key = (selected, category)
    if state.facility_key != facility_key:
        state.facilities = []
        state.facility_key = facility_key
    if st.button('選択駅から1kmの施設を取得', disabled=selected is None):
        try:
            with st.spinner('周辺施設を取得しています…'):
                state.facilities = fetch_facilities(selected, category)
            if not state.facilities:
                st.info('この範囲の登録施設がありません。未登録の施設もあります。')
        except (requests.RequestException, ValueError, KeyError, TypeError):
            state.facilities = []
            st.error('施設を取得できませんでした。再取得するか、下のGoogle マップで確認してください。')
    m = build_map(rows, selected, destination, state.facilities, focus)
    map_data = st_folium(m, height=480, use_container_width=True, key=f'candidate_map_{selected}_{layout}_{category}_{focus}',
                         returned_objects=['last_object_clicked_tooltip'])
    clicked = (map_data or {}).get('last_object_clicked_tooltip')
    if clicked and rows and clicked in names and clicked != state.selected:
        # Update the selector before its next creation, using a pending value.
        state.pending_selection = clicked
        st.rerun()
    st.caption(f'{len(rows)}候補エリア｜青：架空サンプル / 茶：入力物件（選択駅は濃紺）｜家賃帯の下限が予算内のエリアを表示')
    if selected:
        row = next(r for r in rows if r['name'] == selected)
        st.subheader(f'{selected} · {layout} · {row["low"]:g}〜{row["high"]:g}万円 / 月')
        st.caption('入力物件の月額総額範囲' if row['properties'] else '架空サンプル・相場ではありません')
        c1, c2 = st.columns(2)
        c1.metric('ドアツードア（概算）', f'約{row["door"]}分')
        c2.metric('移動距離（概算）', f'{row["distance"]:.1f}km')
        c1, c2 = st.columns(2)
        r = row['route']
        c1.metric('乗り換え', f'{r["transfers"]}回')
        c2.metric('乗車時間（概算）', f'約{math.ceil(r["time"]-r["transfers"]*5)}分')
        groups = []
        for a, b, line in r['legs']:
            if groups and groups[-1][2] == line:
                groups[-1][1] = b
            else:
                groups.append([a, b, line])
        st.write(f'自宅 → 徒歩{home_walk}分 → {selected}')
        for a, b, line in groups:
            st.write(f'{LINES[line]["name"]}：{a} → {b}')
        st.write(f'{destination} → 徒歩{work_walk}分 → 勤務先')
        st.caption('待ち時間3分、乗り換え1回5分を加算。駅間直線距離×1.15・時速40kmの簡易計算。時刻表・急行・直通は未対応。')
        start, end = BY_NAME[selected], BY_NAME[destination]
        params = urlencode({'api': 1, 'origin': f'{start["lat"]},{start["lng"]}',
                            'destination': f'{end["lat"]},{end["lng"]}', 'travelmode': 'transit'})
        st.link_button('実際の時刻・経路を確認', 'https://www.google.com/maps/dir/?'+params)
        st.markdown('**利用路線の朝の混雑率**')
        seen_lines = list(dict.fromkeys(leg[2] for leg in r['legs']))
        if seen_lines:
            st.dataframe([{'路線': LINES[i]['name'], '混雑率': f'{LINES[i]["rate"]}%',
                           '公表区間': LINES[i]['section'], '調査時間帯': LINES[i]['period']} for i in seen_lines],
                         hide_index=True, width='stretch')
        else:
            st.caption('この簡易経路では電車を利用しません。')
        st.caption('2025年度実績。路線の代表的な最混雑区間の1時間平均です。選択経路・方向・時刻の混雑率ではありません。')
        st.link_button('国土交通省の原資料', CROWD_SOURCE)
        if state.facilities:
            st.dataframe([{'施設': f['name'], '駅からの直線距離（m）': f['meters']} for f in state.facilities],
                         hide_index=True, width='stretch')
        st.link_button('周辺施設をGoogle マップで確認', 'https://www.google.com/maps/search/?'+
                       urlencode({'api': 1, 'query': selected+' '+category}))
        st.caption('OpenStreetMap登録施設。駅から半径1km、最大60件。未登録・閉店・位置誤差がある場合があります。')
    with st.expander('候補エリア一覧', expanded=False):
        st.dataframe([{'駅': r['name'], '地域': r['region'], '月額下限（万円）': r['low'],
                       '月額上限（万円）': r['high'], '通勤概算（分）': r['door'],
                       '乗り換え（回）': r['route']['transfers'],
                       '家賃の出所': '入力物件' if r['properties'] else '架空サンプル'} for r in rows],
                     hide_index=True, width='stretch')
    with st.expander('見つけた物件の家賃・管理費を入力'):
        with st.form('property_form', clear_on_submit=True):
            name = st.text_input('物件名', max_chars=60)
            station = st.selectbox('物件の最寄り駅', list(BY_NAME), index=list(BY_NAME).index(selected or '東京'))
            p_layout = st.selectbox('物件の間取り', LAYOUTS, index=LAYOUTS.index(layout))
            rent = st.number_input('家賃（円）', min_value=1, max_value=10_000_000, value=150000, step=1000)
            management = st.number_input('管理費（円）', 0, 10_000_000, 0, 1000)
            common = st.number_input('共益費（円）', 0, 10_000_000, 0, 1000)
            st.caption('「管理費・共益費」と一括記載された同じ費用は、片方だけに入力してください。')
            url = st.text_input('物件ページURL（任意）')
            if st.form_submit_button('月額総額で比較に追加'):
                if not name.strip():
                    st.error('物件名を入力してください。')
                elif url.strip() and not safe_url(url.strip()):
                    st.error('URLはhttpまたはhttpsで入力してください。')
                elif len(state.properties) >= 1000:
                    st.error('入力物件は1,000件までです。')
                else:
                    state.properties.append(dict(id=str(uuid.uuid4()), name=name.strip(), station=station,
                                                 layout=p_layout, rent=rent, management=management,
                                                 common=common, url=url.strip()))
                    state.flash = f'{name.strip()}を追加しました。間取り・予算によって表示対象が変わります。'
                    st.rerun()
    with st.expander('入力物件の確認・削除・CSV保存'):
        st.caption('物件は現在のセッション内で保持します。再接続・再起動で失われる場合があります。CSVで保存・復元してください。')
        if state.properties:
            st.dataframe([{'物件名': p['name'], '駅': p['station'], '間取り': p['layout'],
                           '家賃（円）': p['rent'], '管理費（円）': p['management'], '共益費（円）': p['common'],
                           '月額総額（円）': p['rent']+p['management']+p['common']} for p in state.properties],
                         hide_index=True, width='stretch')
            for p in state.properties:
                if p['url']:
                    st.link_button(p['name']+'：物件ページ', p['url'])
            delete_labels = [f'{i+1}. {p["name"]} / {p["station"]}' for i, p in enumerate(state.properties)]
            delete_label = st.selectbox('削除する物件', delete_labels)
            delete_id = state.properties[delete_labels.index(delete_label)]['id']
            if st.button('選択した物件を削除'):
                state.properties = [p for p in state.properties if p['id'] != delete_id]
                st.rerun()
        st.download_button('入力物件をCSVで保存', export_properties(state.properties), 'sumai_properties.csv', 'text/csv')
        uploaded = st.file_uploader('保存したCSVを読み込む', type=['csv'])
        st.caption('読み込みは追加ではなく、現在の入力物件をCSVの内容で置き換えます。')
        if st.button('CSVの内容で復元する', disabled=uploaded is None):
            try:
                imported = parse_properties(uploaded.getvalue())
                state.properties = imported
                state.flash = f'{len(imported)}件の物件を復元しました。'
                st.rerun()
            except (ValueError, UnicodeError, csv.Error) as error:
                st.error(str(error))
    with st.expander('データと計算方法'):
        st.write('家賃：初期値は架空のサンプルです。入力物件がある駅では、その間取りの入力総額範囲を表示します。家賃帯の下限が予算内なら候補に含みます。')
        st.write('通勤：登録した41駅・11路線で最短時間を探索。距離は駅間直線距離×1.15＋徒歩80m/分です。住所単位の正確なドアツードア検索、最新物件の自動取得には未対応です。')
        st.write('混雑率：国土交通省2025年度実績（2026年7月28日公表）。リアルタイム情報ではありません。')
        st.write('地図・施設：OpenStreetMap / Overpass API。外部サービスへの接続が必要です。施設は30分キャッシュします。')


if __name__ == '__main__':
    # Apply map selections before creating the widget, avoiding Streamlit widget-state errors.
    if 'pending_selection' in st.session_state:
        st.session_state.selected = st.session_state.pop('pending_selection')
    main()
