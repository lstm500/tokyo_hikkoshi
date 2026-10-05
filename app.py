"""住まいコンパス - RESET SAFE MODE v16

目的:
- 起動時の外部通信を完全にゼロにする
- Streamlit内/子プロセスの検索ワーカーを起動しない
- Supabaseは明示ボタンを押した時だけ、現在の中心駅+半径の範囲だけ読む
- 外部検索ワーカーとカスタム地図コンポーネントを使わない

この版は一度アプリを完全に軽量化し、過去データと旧検索プロセスを切り離すための安定化版です。
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
from urllib.parse import urlparse

import streamlit as st


STATIONS = {
    '東京': (35.6812, 139.7671),
    '品川': (35.6285, 139.7388),
    '大崎': (35.6197, 139.7286),
    '五反田': (35.6264, 139.7235),
    '目黒': (35.6339, 139.7158),
    '恵比寿': (35.6467, 139.7101),
    '渋谷': (35.6580, 139.7016),
    '新宿': (35.6909, 139.7003),
    '池袋': (35.7295, 139.7109),
}

LAYOUT_GROUPS = {
    '1K・1L・1DK': ('1K', '1L', '1DK'),
    '1LDK・2DK': ('1LDK', '2DK'),
    '2LDK・3DK': ('2LDK', '3DK'),
    '3LDK': ('3LDK',),
}
TARGET_LAYOUTS = tuple(dict.fromkeys(x for xs in LAYOUT_GROUPS.values() for x in xs))
MAX_BUILDING_AGE = 20


class StorageError(Exception):
    pass


def secret_value(name: str, default: str = '') -> str:
    value = os.environ.get(name)
    if value is None:
        try:
            value = st.secrets.get(name, default)
        except Exception:
            value = default
    return str(value or '').strip()


def supabase_settings():
    url = secret_value('SUPABASE_URL').rstrip('/')
    key = secret_value('SUPABASE_SECRET_KEY') or secret_value('SUPABASE_SERVICE_ROLE_KEY')
    namespace = secret_value('SUPABASE_NAMESPACE', 'sumai-compass')
    if not url or not key:
        raise StorageError('Streamlit Secrets に SUPABASE_URL と SUPABASE_SECRET_KEY を設定してください。')
    try:
        p = urlparse(url)
        valid = (
            p.scheme == 'https' and bool(p.hostname) and p.hostname.endswith('.supabase.co')
            and not p.username and not p.password and p.port in (None, 443)
            and p.path in ('', '/') and not p.query and not p.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise StorageError('SUPABASE_URL を https://プロジェクトID.supabase.co 形式で設定してください。')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', namespace):
        raise StorageError('SUPABASE_NAMESPACE の形式が不正です。')

    headers = {'apikey': key, 'Accept': 'application/json', 'Content-Type': 'application/json'}
    if not key.startswith('sb_secret_'):
        try:
            parts = key.split('.')
            encoded = parts[1]
            payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            if len(parts) != 3 or payload.get('role') != 'service_role':
                raise ValueError()
        except Exception:
            raise StorageError('Secret key（sb_secret_…）または旧service_roleキーを使用してください。') from None
        headers['Authorization'] = 'Bearer ' + key
    return url, headers, namespace


def supabase_get(route: str, params: dict):
    # requests は明示操作時だけ import する。起動時には読み込まない。
    import requests
    url, headers, _ = supabase_settings()
    if route not in {'sumai_properties', 'sumai_jobs'}:
        raise StorageError('Supabase参照先が不正です。')
    try:
        r = requests.get(
            f'{url}/rest/v1/{route}',
            headers=headers,
            params=params,
            timeout=(4, 12),
            allow_redirects=False,
        )
    except requests.RequestException:
        raise StorageError('Supabaseに接続できませんでした。') from None
    if r.status_code in (401, 403):
        raise StorageError('SupabaseのSecret keyまたは権限を確認してください。')
    if r.status_code == 404:
        raise StorageError('Supabase v05のテーブルがありません。セットアップSQLを確認してください。')
    if not 200 <= r.status_code < 300:
        raise StorageError(f'Supabaseの読み込みに失敗しました（HTTP {r.status_code}）。')
    try:
        data = r.json()
    except ValueError:
        raise StorageError('Supabaseの応答を読み取れませんでした。') from None
    if not isinstance(data, list):
        raise StorageError('Supabaseの応答形式が不正です。')
    return data


def bounds_from_center(lat: float, lng: float, radius_m: int):
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * max(0.2, math.cos(math.radians(lat))))
    return [lat - dlat, lng - dlng, lat + dlat, lng + dlng]


def distance_m(lat1, lng1, lat2, lng2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(min(1.0, math.sqrt(a)))


def parse_property(row):
    if not isinstance(row, dict):
        return None
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else row
    if not isinstance(payload, dict):
        return None
    try:
        p = dict(payload)
        p['lat'] = float(p['lat'])
        p['lng'] = float(p['lng'])
        p['rent'] = int(float(p['rent']))
        p['fees'] = int(float(p.get('fees', 0)))
        p['area'] = float(p['area'])
        if p.get('building_age') not in ('', None):
            p['building_age'] = int(float(p['building_age']))
    except (KeyError, TypeError, ValueError):
        return None
    if p.get('layout') not in TARGET_LAYOUTS:
        return None
    if str(p.get('structure', '')).strip().upper() != 'SRC':
        return None
    age = p.get('building_age')
    if age in ('', None) or not 0 <= int(age) <= MAX_BUILDING_AGE:
        return None
    return p


def load_range(center_lat: float, center_lng: float, radius_m: int, layouts):
    _, _, namespace = supabase_settings()
    south, west, north, east = bounds_from_center(center_lat, center_lng, radius_m)
    params = {
        'namespace': 'eq.' + namespace,
        'select': 'payload',
        'and': f'(lat.gte.{south},lat.lte.{north},lng.gte.{west},lng.lte.{east})',
        'structure': 'eq.SRC',
        'building_age': f'lte.{MAX_BUILDING_AGE}',
        'layout': 'in.(' + ','.join(layouts) + ')',
        'order': 'fetched_at.desc',
        'limit': 2000,
    }
    rows = supabase_get('sumai_properties', params)
    out = []
    seen = set()
    for row in rows:
        p = parse_property(row)
        if not p:
            continue
        if distance_m(center_lat, center_lng, p['lat'], p['lng']) > radius_m:
            continue
        identity = p.get('url') or p.get('id') or (round(p['lat'], 6), round(p['lng'], 6), p.get('layout'), p.get('rent'))
        if identity in seen:
            continue
        seen.add(identity)
        out.append(p)
    return out


def main():
    st.set_page_config(page_title='住まいコンパス', page_icon='🏠', layout='wide')
    st.markdown('''<style>
    .stApp{background:white;color:#173a5e;color-scheme:light}
    .block-container{padding-top:1rem;padding-bottom:2rem;max-width:1200px}
    h1,h2,h3,p,label{color:#173a5e}
    </style>''', unsafe_allow_html=True)

    st.title('住まいコンパス')
    st.caption('RESET SAFE MODE v16')
    st.info('この版は起動時にSupabase・物件データ・地図コンポーネント・検索プロセスへ一切アクセスしません。')

    state = st.session_state
    state.setdefault('records', [])

    with st.expander('表示設定', expanded=True):
        station = st.selectbox('中心駅', list(STATIONS), index=list(STATIONS).index('池袋'))
        layout_group = st.radio('間取り区分', list(LAYOUT_GROUPS), index=1, horizontal=True)
        radius = st.selectbox('表示半径', [500, 1000, 1500, 2000, 3000], index=2, format_func=lambda x: f'{x/1000:g}km')
        budget = st.number_input('物件ピンの月額上限（万円）', 1.0, 1000.0, 50.0, 1.0)

    lat, lng = STATIONS[station]
    c1, c2 = st.columns(2)
    if c1.button('Supabase接続だけ確認'):
        try:
            _, _, namespace = supabase_settings()
            rows = supabase_get('sumai_properties', {
                'namespace': 'eq.' + namespace,
                'select': 'identity',
                'limit': 1,
            })
            st.success(f'Supabase接続OK。現在の確認結果: {len(rows)}件取得可能。')
        except StorageError as e:
            st.error(str(e))

    if c2.button('この範囲の保存済み物件だけ読み込む', type='primary'):
        try:
            state.records = load_range(lat, lng, radius, LAYOUT_GROUPS[layout_group])
            st.success(f'{len(state.records)}件を読み込みました。')
        except StorageError as e:
            st.error(str(e))

    records = [p for p in state.records if p.get('layout') in LAYOUT_GROUPS[layout_group]]
    displayed = [p for p in records if p['rent'] + p['fees'] <= budget * 10000]

    # Streamlit標準の地図だけを使う。streamlit-foliumは完全撤去。
    if displayed:
        points = [{'lat': p['lat'], 'lon': p['lng']} for p in displayed]
        st.map(points, latitude='lat', longitude='lon', use_container_width=True)
        st.dataframe([
            {
                '物件名': p.get('name', ''),
                '間取り': p.get('layout', ''),
                '総額（万円）': round((p['rent'] + p['fees']) / 10000, 2),
                '面積（㎡）': p.get('area', ''),
                '築年数': p.get('building_age', ''),
                '住所': p.get('address', ''),
                '地図判定住所': p.get('map_address', ''),
                '位置精度': p.get('location_confidence', ''),
                '募集ページ': p.get('url', ''),
            }
            for p in displayed
        ], hide_index=True, use_container_width=True,
           column_config={'募集ページ': st.column_config.LinkColumn('募集ページ')})
    else:
        st.caption('まだ表示用データを読み込んでいません。リセット直後は0件で正常です。')

    st.divider()
    st.warning('高負荷検索機能はこのRESET版では意図的に停止しています。子プロセス・ThreadPool・自動再開は一切ありません。')
    st.caption('まずこの版が即時起動することを確認してください。過去データを消した後も起動が重い場合、検索コードではなくStreamlit Cloud側のコンテナ状態・ビルド状態を切り分けできます。')


if __name__ == '__main__':
    main()
