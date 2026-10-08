"""住まいコンパス / REBUILD 01 — complete independent implementation.
Run: streamlit run app.py
Dependencies: streamlit>=1.50,<2, requests>=2.32,<3, beautifulsoup4>=4.13,<5, folium>=0.18,<1, streamlit-folium==0.24.0, numpy>=1.26,<3, Pillow>=10,<12.
Persistent storage: Supabase housing_units_v1, housing_searches_v1, housing_places_v1.
Search and available-field storage run on server worker threads; UI polls snapshots.
New flexible listings are JSON records in housing_searches_v1; old housing_units_v1 records remain readable.
"""
from __future__ import annotations

import base64
import concurrent.futures as futures
import csv
import hashlib
import heapq
import html
import io
import json
import math
import os
import calendar
import queue
import re
import statistics
import tempfile
import traceback
import threading
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, quote

from branca.element import MacroElement, Template
import folium
import requests
import streamlit as st
from streamlit_folium import st_folium
from bs4 import BeautifulSoup

BUILD = "REBUILD-01-v66"
PROVIDER_OPTIONS = ("スマイティ","HOMES","SUUMO","カナリー","アットホーム","CHINTAI","Comfy","アパマンショップ")
PROVIDER_CANONICAL = {"HOMES":"HOME’S","HOME’S":"HOME’S","スマイティ":"スマイティ","SUUMO":"SUUMO","カナリー":"カナリー",
                      "アットホーム":"アットホーム","CHINTAI":"CHINTAI","Comfy":"Comfy","アパマンショップ":"アパマンショップ"}
PROVIDER_DISPLAY = {"HOME’S":"HOMES",**{x:x for x in PROVIDER_OPTIONS if x!="HOMES"}}
PROVIDER_HOST = {
    "スマイティ":"sumaity.com","HOME’S":"www.homes.co.jp","SUUMO":"suumo.jp","カナリー":"web.canary-app.jp",
    "アットホーム":"www.athome.co.jp","CHINTAI":"www.chintai.net","Comfy":"comfy.maison","アパマンショップ":"www.apamanshop.com",
}
RENTAL_HOSTS = frozenset(PROVIDER_HOST.values())
# Concurrency is increased across independent tasks/hosts only. Per-host request
# spacing and in-flight limits remain conservative, so faster collection comes
# from overlap, caching and fewer duplicate requests rather than hitting one site harder.
SEARCH_TASK_WORKERS = 6
DETAIL_WORKERS = 6
DETAIL_PAGE_WORKERS = 4
PER_HOST_SLOTS = 2
PER_HOST_MIN_INTERVAL = 1.0
ADDRESS_NEGATIVE_CACHE_SECONDS = 900
PREF_ALIAS = {"11":"saitama","12":"chiba","13":"tokyo","14":"kanagawa"}
DEFAULT_CENTER = (35.7303,139.711)
UNIT_TABLE = "housing_units_v1"
SEARCH_TABLE = "housing_searches_v1"
PLACE_TABLE = "housing_places_v1"
ADDRESS_POINT_KIND = "saved_address_point_v1"
GROUPS = {"1K・1L・1DK": ("1K", "1L", "1DK"), "1LDK・2DK": ("1LDK", "2DK"),
          "2LDK・3DK": ("2LDK", "3DK"), "3LDK": ("3LDK",)}
LAYOUTS = tuple(x for group in GROUPS.values() for x in group)
SUUMO_LAYOUT_GROUPS = (("1LDK", "2K", "2DK"), ("2LDK", "3K", "3DK"))
SUUMO_LAYOUTS = tuple(x for group in SUUMO_LAYOUT_GROUPS for x in group)
SUUMO_LAYOUT_CODES = {"1LDK":"04","2K":"05","2DK":"06","2LDK":"07","3K":"08","3DK":"09"}
HOMES_LAYOUT_GROUPS = (("1LDK", "2K", "2DK"), ("2LDK", "3K", "3DK"))
DISPLAY_LAYOUT_GROUPS = {"group1": ("1LDK", "2K", "2DK"), "group2": ("2LDK", "3K", "3DK")}
HOMES_LAYOUTS = tuple(x for group in HOMES_LAYOUT_GROUPS for x in group)
ALL_TARGET_LAYOUTS = tuple(dict.fromkeys((*LAYOUTS,*SUUMO_LAYOUTS,*HOMES_LAYOUTS,'1SLDK','2SLDK','3SLDK','1SDK','2SDK','3SDK','1SK')))
STATIONS = {
    "東京": (35.6812,139.7671), "品川":(35.6285,139.7388), "大崎":(35.6197,139.7286),
    "五反田":(35.6264,139.7235), "目黒":(35.6339,139.7158), "恵比寿":(35.6467,139.7101),
    "渋谷":(35.6580,139.7016), "新宿":(35.6909,139.7003), "池袋":(35.7295,139.7109),
    "上野":(35.7138,139.7770), "秋葉原":(35.6984,139.7731), "赤羽":(35.7780,139.7209),
    "川口":(35.8019,139.7175), "浦和":(35.8585,139.6571), "大宮":(35.9063,139.6238),
    "大井町":(35.6063,139.7346), "蒲田":(35.5625,139.7160), "川崎":(35.5313,139.6970),
    "横浜":(35.4662,139.6220), "武蔵小杉":(35.5750,139.6595), "日吉":(35.5530,139.6468),
    "自由が丘":(35.6074,139.6685), "中目黒":(35.6443,139.6990), "中野":(35.7058,139.6658),
    "荻窪":(35.7045,139.6201), "吉祥寺":(35.7032,139.5797), "三鷹":(35.7027,139.5603),
    "国分寺":(35.7001,139.4808), "立川":(35.6982,139.4137), "錦糸町":(35.6960,139.8140),
    "新小岩":(35.7169,139.8586), "市川":(35.7289,139.9084), "船橋":(35.7017,139.9850),
    "津田沼":(35.6907,140.0207), "千葉":(35.6134,140.1133), "北千住":(35.7494,139.8050),
    "松戸":(35.7847,139.9007), "柏":(35.8622,139.9711), "二子玉川":(35.6117,139.6267),
    "溝の口":(35.5998,139.6115), "南浦和":(35.8476,139.6690),
}
LINES = {
    "山手線": "東京 品川 大崎 五反田 目黒 恵比寿 渋谷 新宿 池袋 上野 秋葉原 東京".split(),
    "京浜東北線": "大宮 浦和 南浦和 川口 赤羽 上野 秋葉原 東京 品川 大井町 蒲田 川崎 横浜".split(),
    "埼京線": "大宮 赤羽 池袋 新宿 渋谷 恵比寿 大崎".split(),
    "中央線快速": "東京 新宿 中野 荻窪 吉祥寺 三鷹 国分寺 立川".split(),
    "東海道線": "東京 品川 川崎 横浜".split(), "横須賀線": "東京 品川 武蔵小杉 横浜".split(),
    "東横線": "渋谷 中目黒 自由が丘 武蔵小杉 日吉 横浜".split(),
    "総武線快速": "東京 錦糸町 新小岩 市川 船橋 津田沼 千葉".split(),
    "常磐線快速": "上野 北千住 松戸 柏".split(), "南武線": "川崎 武蔵小杉 溝の口 立川".split(),
    "田園都市線": "渋谷 二子玉川 溝の口".split(),
}
CROWD_SOURCE = "https://www.mlit.go.jp/report/press/content/002015142.pdf"
# 国土交通省・2025年度。公表区間の最混雑1時間、経路全体の予測ではない。
CROWD = {
    '山手線':(136,'新大久保 → 新宿','7:44〜8:44'),
    '京浜東北線':(158,'川口 → 赤羽','7:20〜8:20'),
    '埼京線':(167,'板橋 → 池袋','7:51〜8:51'),
    '中央線快速':(158,'中野 → 新宿','7:35〜8:35'),
    '東海道線':(159,'川崎 → 品川','7:39〜8:39'),
    '横須賀線':(138,'武蔵小杉 → 西大井','7:26〜8:26'),
    '東横線':(124,'祐天寺 → 中目黒','7:50〜8:50'),
    '総武線快速':(160,'新小岩 → 錦糸町','7:31〜8:31'),
    '常磐線快速':(142,'三河島 → 日暮里','7:26〜8:26'),
    '南武線':(156,'武蔵中原 → 武蔵小杉','7:30〜8:30'),
    '田園都市線':(138,'池尻大橋 → 渋谷','7:50〜8:50'),
}
BANDS = (100000,125000,150000,175000,200000,225000,250000,275000,300000,350000,400000)
# Perceptual rent scale. The five bands up to 200,000 yen deliberately use clearly
# separated hues (navy -> blue -> cyan -> green -> yellow). Above 200,000 yen the
# scale stays in a warm orange-to-burgundy family so the lower-price differences remain
# immediately readable on the grayscale base map.
COLORS = ("#17357A","#0067D9","#00A9D6","#00A870","#E8B900","#F39A1F",
          "#F06B24","#E94A35","#D9342B","#BC2733","#941D3B","#65102F")
RENT_BAND_LABELS = (f'{BANDS[0]/10000:g}万円以下',) + tuple(
    f'{lower/10000:g}〜{upper/10000:g}万円' for lower,upper in zip(BANDS,BANDS[1:])
) + (f'{BANDS[-1]/10000:g}万円超',)
SQL = '''-- 新しいアプリ専用。既存の物件・ジョブ・テーブルは削除しません。
create table if not exists public.housing_units_v1 (
 namespace text not null, key text not null, title text not null,
 address text not null default '', latitude double precision not null,
 longitude double precision not null, layout text not null,
 rent integer not null check (rent > 0), fees integer not null default 0 check (fees >= 0),
 area double precision not null check (area > 0), floor text not null default '',
 structure text not null check (structure = 'SRC'), age integer not null check (age between 0 and 20),
 built_ym text, location_method text not null, map_address text not null default '',
 address_match text not null default '', provider text not null,
 listing_url text not null, fetched_at timestamptz not null,
 primary key(namespace, key),
 check(latitude between 34 and 37), check(longitude between 138 and 141)
);
create index if not exists housing_units_v1_range on public.housing_units_v1(namespace,latitude,longitude);
create table if not exists public.housing_searches_v1 (
 namespace text not null, id text not null, status text not null,
 started_at timestamptz not null, finished_at timestamptz,
 conditions jsonb not null, summary jsonb not null default '{}'::jsonb,
 primary key(namespace,id)
);
create table if not exists public.housing_places_v1 (
 namespace text not null, key text not null, title text not null, kind text not null,
 latitude double precision not null, longitude double precision not null,
 fetched_at timestamptz not null, primary key(namespace,key),
 check(latitude between 34 and 37), check(longitude between 138 and 141)
);
create index if not exists housing_places_v1_range on public.housing_places_v1(namespace,latitude,longitude);
alter table public.housing_places_v1 enable row level security;
alter table public.housing_units_v1 enable row level security;
alter table public.housing_searches_v1 enable row level security;
grant usage on schema public to service_role;
grant all on public.housing_units_v1, public.housing_searches_v1, public.housing_places_v1 to service_role;
notify pgrst, 'reload schema';
'''
CSS = '''<style>
.stApp,[data-testid="stAppViewContainer"]{background:#f5f3ed!important;color:#203b38!important;color-scheme:light}
[data-testid="stHeader"]{background:#f5f3ed!important}
.block-container{max-width:1150px;padding:1.4rem 1rem 3rem}
h1,h2,h3,label,p{color:#203b38}
.hero{background:#203f39;border-radius:22px;padding:24px;margin-bottom:20px}
.hero h1,.hero p,.hero span{color:#fff!important}
.hero h1{font-size:30px;margin:10px 0}.hero p{font-size:14px;line-height:1.8}
.badge{font-size:11px;letter-spacing:2px;background:#41635a;border-radius:12px;padding:5px 9px}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button,[data-testid="stFormSubmitButton"] button{
 background:#fff!important;color:#203f39!important;border:1px solid #a7b8ac!important;border-radius:12px!important;min-height:44px}
[data-testid="stButton"] button *,[data-testid="stDownloadButton"] button *,[data-testid="stFormSubmitButton"] button *{color:inherit!important}
button[kind="primary"],button[kind="primaryFormSubmit"]{background:#c55c42!important;color:#fff!important;border-color:#c55c42!important}
button:disabled{background:#e3e9e3!important;color:#607369!important;opacity:1!important}
[data-baseweb="select"]>div,[data-baseweb="input"],input,[data-testid="stNumberInput"] button{background:#fff!important;color:#203f39!important}
[data-baseweb="select"] span,[role="option"],[role="listbox"]{color:#203f39!important;background:#fff!important}
[data-testid="stMetric"]{background:#fff;border:1px solid #e0e4da;border-radius:16px;padding:15px}
[data-testid="stCode"] pre,[data-testid="stCode"] code{background:#edf0e8!important;color:#203f39!important}
[data-testid="stForm"]{border:1px solid #d1dccf;border-radius:18px;background:#fff;padding:18px}
[data-testid="stSidebar"]{background:#e8ece3!important}
[data-baseweb="tab-list"],[data-baseweb="tab"]{background:#f5f3ed!important;color:#203f39!important}
[data-testid="stExpander"] details{background:#fff!important;color:#203f39!important}
[data-baseweb="select"] svg{fill:#203f39!important}
[data-testid="stFileUploader"] section{background:#fff!important;color:#203f39!important}
.unit{background:#fff;border-radius:16px;padding:18px;margin-bottom:12px;border:1px solid #e0e4da}
.unit h3{font-size:18px;margin:0 0 8px}.unit a{color:#286b5d}
@media(max-width:600px){.hero{padding:18px}.hero h1{font-size:26px}.block-container{padding-top:1rem}}
</style>'''


class SearchCancelled(BaseException):
    """Cooperative stop; collector error recovery must not swallow it."""


class AppError(Exception):
    """User-safe connection or validation failure."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normal(value):
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def address_key(value):
    value=normal(value).replace(' ','').replace('　','')
    digits={'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    def convert(match):
        text=match[1]
        if '十' in text:
            head,tail=text.split('十',1)
            number=(digits.get(head,1)*10)+digits.get(tail,0)
        else:
            number=digits.get(text)
        return (str(number) if number is not None else text)+'丁目'
    return re.sub(r'([一二三四五六七八九十]+)丁目',convert,value)



def saved_address_point_key(address):
    value=normal(address)
    return hashlib.sha256(('saved-address-point-v1\0'+value).encode('utf-8')).hexdigest() if value else None



def town_matches(town,address):
    wanted=address_key(town);actual=address_key(address)
    match=re.fullmatch(r'(.*?)(\d+)丁目',wanted)
    if match:
        base,number=match.groups()
        return bool(re.search(r'(?:^|[都道府県市区郡町村])'+re.escape(base+number)+r'(?:丁目|[-ー−－番号]|$)',actual))
    return bool(re.search(r'(?:^|[都道府県市区郡町村])'+re.escape(wanted)+r'(?=\d|[-ー−－番号]|$)',actual))


def parsed_layout(raw,rough=True):
    value=normal(raw).upper().replace('　',' ').strip()
    match=re.match(r'^(\d+(?:LDK|SLDK|SDK|DK|SK|LK|K|L|R))(?=$|[\s(（+/])',value)
    return (re.sub(r'^(\d+)S(LDK|DK|K)$',r'\1\2',match[1]) if rough else match[1]) if match else '1R' if value=='ワンルーム' else None


def resolve_layout(web,raw,requested=None,url=''):
    observed=parsed_layout(raw)
    if observed:
        if observed not in ALL_TARGET_LAYOUTS or requested and observed!=requested:
            trace(web,'layout',{'url':url,'observed':raw,'parsed':observed,'requested':requested,'reason':'explicit_layout_does_not_match_search'});return None
        return observed
    if requested in ALL_TARGET_LAYOUTS:
        trace(web,'layout_assumed',{'url':url,'observed':raw,'assigned':requested,'source':'single_layout_search','confirmed_from_listing':False},'WARNING');return requested
    trace(web,'layout',{'url':url,'observed':raw,'reason':'no_readable_layout_and_no_single_layout_search'},'WARNING');return None


def discover_layout_inputs(soup,provider):
    result={}
    for field in soup.select('input[name],select[name] option[value]'):
        name=field.get('name') or (field.parent.get('name') if field.parent else '')
        if not name or not ('madori' in name.lower() if provider=='HOME’S' else name=='md'):continue
        options=[]
        if field.get('id'):
            label=soup.find('label',attrs={'for':field['id']})
            if label:options.append(label.get_text(' ',strip=True))
        label=field.find_parent('label')
        if label:options.append(label.get_text(' ',strip=True))
        if field.name=='option':options.append(field.get_text(' ',strip=True))
        options.extend([field.get('aria-label',''),field.get('data-label','')])
        for text in options:
            layout=parsed_layout(text,rough=False)
            allowed=ALL_TARGET_LAYOUTS
            if layout in allowed and str(field.get('value','')):
                result[layout]=(name,str(field['value']));break
    return result


def single_layout_parameters(web,provider,requested):
    """Read public form codes rather than guessing undocumented provider identifiers."""
    allowed=ALL_TARGET_LAYOUTS
    if requested not in allowed:raise AppError('単独検索する間取りを確認してください。')
    lock=getattr(web,'layout_form_lock',None)
    if lock is None:lock=threading.Lock();web.layout_form_lock=lock
    with lock:
        cache=getattr(web,'layout_form_cache',None)
        if cache is None:cache={};web.layout_form_cache=cache
        if provider not in cache:
            urls=['https://www.homes.co.jp/chintai/tokyo/list/','https://www.homes.co.jp/chintai/tokyo/','https://www.homes.co.jp/chintai/theme/14130/tokyo/list/'] if provider=='HOME’S' else ['https://suumo.jp/jj/chintai/ichiran/FR301FC001/','https://suumo.jp/chintai/tokyo/']
            reasons=[]
            for url in urls:
                try:
                    if not web.permitted(url):raise AppError('間取り条件フォームの自動取得は許可されていません。')
                    reply=web.fetch(url,params={'ar':'030','bs':'040','ta':'13'} if '/FR301FC001/' in url else {})
                    filters=discover_layout_inputs(BeautifulSoup(reply.text,'html.parser'),provider)
                    if not filters:raise AppError(provider+'の間取り条件フォームを読み取れません。')
                    cache[provider]=filters
                    trace(web,'layout_filter_discovered',{'provider':provider,'source_url':url,'filters':filters},stage='search_conditions');break
                except AppError as exc:
                    reasons.append(str(exc));trace(web,'form_alternative',{'provider':provider,'failed_url':url,'message':str(exc),'next_public_page':url!=urls[-1]},'WARNING','search_conditions')
            if provider not in cache:cache[provider]='／'.join(dict.fromkeys(reasons))+' 単独条件を確認できないため一括検索へ切り替えません。'
        filters=cache[provider]
        if isinstance(filters,str):raise AppError(filters)
        if requested not in filters:raise AppError(provider+'では'+requested+'の単独間取り条件を確認できません。他の間取りの検索は続けます。')
        name,value=filters[requested]
        if provider=='HOME’S':
            # Public form and map request share the madori identifiers; preserve names already under cond.
            name=name if name.startswith('cond[') else 'cond[madori]['+value+']'
        params={name:value}
        trace(web,'single_layout_query',{'provider':provider,'requested':requested,'parameters':params,'source':'public_form'},stage='search_conditions')
        return params

def meters(a, b):
    a1,b1,a2,b2 = map(math.radians, (*a,*b))
    h = math.sin((a2-a1)/2)**2 + math.cos(a1)*math.cos(a2)*math.sin((b2-b1)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(min(1,max(0,h))))


def rectangle(center, radius):
    lat,lng=center;dy=radius/111320;dx=radius/(111320*math.cos(math.radians(lat)))
    return lat-dy,lng-dx,lat+dy,lng+dx


def in_rectangle(point, bounds):
    return bounds[0]<=point[0]<=bounds[2] and bounds[1]<=point[1]<=bounds[3]


def viewport_bounds(data):
    try:
        b=data['bounds'];s=float(b['_southWest']['lat']);w=float(b['_southWest']['lng'])
        n=float(b['_northEast']['lat']);e=float(b['_northEast']['lng'])
        if not all(math.isfinite(v) for v in (s,w,n,e)) or not (34<=s<n<=37 and 138<=w<e<=141):
            return None
        return (s,w,n,e)
    except (KeyError,TypeError,ValueError): return None


def bounds_center(bounds):
    s,w,n,e=bounds
    return ((s+n)/2,(w+e)/2)


def bounds_radius(bounds):
    return max(meters(bounds_center(bounds),p) for p in ((bounds[0],bounds[1]),(bounds[2],bounds[3])))


def tiles_in_bounds(bounds,zoom=15):
    def tile(lat,lng):
        size=2**zoom
        return int((lng+180)/360*size),int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*size)
    s,w,n,e=bounds;x1,y1=tile(n,w);x2,y2=tile(s,e)
    return [(x,y) for x in range(x1,x2+1) for y in range(y1,y2+1)]


def bounded_results(items,fn,workers=6,stop_event=None):
    """Bound simultaneous requests, never the number of items searched."""
    iterator=iter(items)
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending={}
        def fill():
            while len(pending)<workers:
                try: item=next(iterator)
                except StopIteration: break
                pending[pool.submit(fn,item)]=item
        fill()
        while pending:
            if stop_event is not None and stop_event.is_set():
                for f in pending:f.cancel()
                raise SearchCancelled()
            ready,_=futures.wait(pending,timeout=.4,return_when=futures.FIRST_COMPLETED)
            if not ready: yield None,None,None
            for f in ready:
                item=pending.pop(f)
                try: yield item,f.result(),None
                except Exception as exc: yield item,None,exc
            fill()


def amount(value):
    text=normal(value).replace(",", "").replace(" ", "")
    if text in ("", "-", "―", "なし", "無"): return 0
    match=re.fullmatch(r"(\d+(?:\.\d+)?)(万円|円)?(?:/月)?",text)
    if not match: raise AppError("金額を読み取れません")
    return round(float(match[1])*(10000 if match[2]=="万円" else 1))


def age_info(value):
    text=normal(value); now=datetime.now(timezone.utc)
    date=re.search(r"((?:19|20)\d{2})年\s*(\d{1,2})月",text)
    if date:
        y,m=map(int,date.groups())
        if not 1<=m<=12: return None,None
        age=now.year-y-int(now.month<m)
        return age,f"{y:04d}-{m:02d}"
    age=re.search(r"築\s*(\d{1,3})年",text)
    if age: return int(age[1]),None
    if "新築" in text: return 0,None
    return None,None


def is_src(value):
    value=normal(value).upper().replace(" ","")
    return "鉄骨鉄筋" in value or bool(re.search(r"(?<![A-Z])SRC(?![A-Z])",value))


def monthly_price(row):
    return float(row.get('rent') or 0)+float(row.get('fees') or 0)


def has_point(row):
    try:return math.isfinite(float(row['latitude'])) and math.isfinite(float(row['longitude'])) and 34<=float(row['latitude'])<=37 and 138<=float(row['longitude'])<=141
    except (ValueError,TypeError,KeyError):return False


def listing_in_bounds(row,bounds):
    if bounds is None:return True
    if has_point(row):return in_rectangle((row['latitude'],row['longitude']),bounds)
    old=row.get('search_bounds')
    return isinstance(old,(list,tuple)) and len(old)==4 and old[0]<=bounds[2] and old[2]>=bounds[0] and old[1]<=bounds[3] and old[3]>=bounds[1]


def acquisition_time_jst(value):
    if not value:return '未記録'
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None:return '未記録'
        return dt.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST')
    except (ValueError,TypeError):return '未記録'


def compact_saved_listing(row):
    """Persistent listing payload: rent, layout, inferred address and acquisition time."""
    try:
        rent=row.get('rent');layout=normal(row.get('layout'));address=normal(row.get('address') or row.get('inferred_address'))
        if not isinstance(rent,(int,float)) or not math.isfinite(float(rent)) or float(rent)<=0:return None
        if layout not in ALL_TARGET_LAYOUTS or not address:return None
        stamp=row.get('fetched_at')
        if stamp:
            parsed=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
            if parsed.tzinfo is None:return None
            stamp=parsed.astimezone(timezone.utc).isoformat(timespec='seconds')
        result={'rent':int(round(float(rent))),'layout':layout,'address':address[:220],'fetched_at':stamp or None}
        property_id=row.get('property_id')
        if isinstance(property_id,str) and re.fullmatch(r'SUUMO:(?:bc|jnc):\d+',property_id):result['property_id']=property_id
        return result
    except (ValueError,TypeError,AttributeError):return None


def newest_observation(old,new):
    if not old:return new
    try:
        a=datetime.fromisoformat(str(old.get('fetched_at')).replace('Z','+00:00'))
        b=datetime.fromisoformat(str(new.get('fetched_at')).replace('Z','+00:00'))
        if a.tzinfo and b.tzinfo and a>b:return old
    except (ValueError,TypeError):pass
    return new


def compact_listing_key(row):
    compact=compact_saved_listing(row)
    if not compact:return None
    identity={'property_id':compact['property_id']} if compact.get('property_id') else {k:compact[k] for k in ('rent','layout','address')}
    return hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def valid_unit(row):
    try:
        compact=compact_saved_listing(row)
        if not compact:return False
        if not row.get('key'):return False
        provider=row.get('provider');url=row.get('listing_url')
        return not (provider or url) or bool(provider and url and safe_url(url,provider))
    except (ValueError,TypeError,KeyError):return False


def optional_amount(value):
    if value is None or not str(value).strip():return None
    try:return amount(value)
    except (AppError,ValueError,TypeError):return None


def partial_listing(provider,url,title,address,layout,rent,fees,area,region,bounds,location=None,structure=None,age=None,built_ym=None,floor=''):
    def number(value,minimum,maximum):
        try:return value if value is not None and math.isfinite(float(value)) and minimum<=float(value)<=maximum else None
        except (ValueError,TypeError):return None
    rent=number(rent,1,float('inf'));fees=number(fees,0,float('inf'));area=number(area,.01,10000);age=number(age,0,150)
    point=region.get('point') if region else None
    precise=bool(location and has_point(location))
    location=dict(location or {})
    # SUUMO/HOMES must never manufacture a property position from the search town or a textual address.
    # Their saved address must come from a provider map location (SUUMO marker / HOMES image pin).
    if provider in ('HOME’S','HOMES','SUUMO') and not precise:
        return None
    if not precise:
        okay=isinstance(point,(list,tuple)) and len(point)==2 and in_rectangle(point,bounds)
        location=dict(latitude=point[0] if okay else None,longitude=point[1] if okay else None,
                      location_method='町丁目の検索地点（建物位置未確認）' if okay else '地域のみ確認・地図位置未確認',
                      map_address=region.get('label','') if region else '',address_match='町丁目単位')
    row=dict(key=hashlib.sha256(url.encode()).hexdigest(),title=str(title or '掲載募集'),address=str(address or ''),layout=layout,
             rent=rent if rent and rent>0 else None,fees=fees,area=area,floor=floor,structure=structure,age=age,built_ym=built_ym,
             provider=provider,listing_url=url,fetched_at=utc_now(),coordinate_precision=location.pop('coordinate_precision','building') if precise else 'town',
             region_label=region.get('label','') if region else '',search_bounds=list(bounds),**location)
    row['price_basis']='管理費込み' if fees is not None else '家賃のみ・管理費未確認'
    row['missing_fields']=[k for k in ('rent','fees','area','structure','age') if row.get(k) is None]
    if not precise:row['missing_fields'].append('building_location')
    return row if valid_unit(row) else None


def merge_listing(old,new):
    """Incomplete new observations must not erase previously acquired fields or map points."""
    if not old:return dict(new)
    if newest_observation(old,new) is old:return dict(old)
    out=dict(old);retained=[]
    for k,v in new.items():
        if v is not None and v!='' and k!='missing_fields':out[k]=v
        elif old.get(k) is not None and old.get(k)!='':retained.append(k)
    rank={'town':1,'address':2,'building':3,'listing_map':4}
    if has_point(old) and (not has_point(new) or rank.get(old.get('coordinate_precision'),0)>rank.get(new.get('coordinate_precision'),0)):
        for k in ('latitude','longitude','coordinate_precision','location_method','map_address','inferred_address','position_source_url','address_precision','address_match'):
            if k in old:out[k]=old[k];retained.append(k)
    elif has_point(new) and ((new.get('latitude'),new.get('longitude'))!=(old.get('latitude'),old.get('longitude')) or new.get('position_source_url')!=old.get('position_source_url')):
        for k in ('map_address','inferred_address','address_precision','address_match','position_source_url','location_method'):
            out[k]=new.get(k,'')
    a,b=address_key(old.get('address','')),address_key(new.get('address',''))
    if a and b and b in a and len(a)>len(b):out['address']=old['address'];retained.append('address')
    out['missing_fields']=[k for k in ('rent','fees','area','structure','age') if out.get(k) is None]
    if not has_point(out) or out.get('coordinate_precision') in ('town','address'):out['missing_fields'].append('building_location')
    out['price_basis']='管理費込み' if out.get('fees') is not None else '家賃のみ・管理費未確認'
    if retained:out['retained_fields_from_previous']=sorted(set(retained))
    return out


def rental_network_settings():
    def setting(name,default=None):
        value=os.environ.get(name)
        if value is None:
            try:value=st.secrets.get(name,default)
            except Exception:value=default
        return value
    values=setting('RENTAL_HTTP_PROXIES',[]) or []
    if isinstance(values,str):values=[x.strip() for x in values.splitlines() if x.strip()]
    single=setting('RENTAL_HTTP_PROXY','')
    if single:values=[single,*values]
    if not isinstance(values,(list,tuple)):raise AppError('RENTAL_HTTP_PROXIESを接続URLの配列で設定してください。')
    routes=[]
    for value in values:
        value=str(value).strip()
        try:
            u=urlparse(value)
            if u.scheme not in ('http','https') or not u.hostname or u.port is None or u.query or u.fragment or any(c.isspace() for c in value):raise ValueError()
        except ValueError:raise AppError('プロキシURLは http://ユーザー:パスワード@ホスト:ポート の形式で設定してください。') from None
        if value not in routes:routes.append(value)
    return {'proxies':routes}


@st.cache_resource
def saved_position_memory():
    return {'points':{},'flights':{},'lock':threading.RLock(),
            'http':{'gates':{},'slots':{},'starts':{},'backoff':{},'lock':threading.RLock()}}


def remember_saved_position(memory,address,point):
    if not memory or not point:return
    try:
        lat,lng,title=point
        if not has_point({'latitude':lat,'longitude':lng}):return
    except (ValueError,TypeError):return
    with memory['lock']:
        points=memory['points']
        if len(points)>=12000 and normal(address) not in points:points.pop(next(iter(points)))
        points[normal(address)]=(time.monotonic(),(float(lat),float(lng),str(title)))


def cached_saved_position(memory,address):
    if not memory:return None
    with memory['lock']:
        entry=memory['points'].get(normal(address))
        return entry[1] if entry and time.monotonic()-entry[0]<86400 else None


class Database:
    """Fresh typed storage; no legacy tables/payloads or job lookups."""
    def __init__(self, config=None):
        def setting(name,default=""):
            value=config.get(name,default) if config is not None else os.environ.get(name)
            if value is None:
                try: value=st.secrets.get(name,default)
                except Exception: value=default
            return str(value or "").strip()
        self.url=setting('SUPABASE_URL').rstrip('/')
        self.key=setting('SUPABASE_SECRET_KEY') or setting('SUPABASE_SERVICE_ROLE_KEY')
        self.namespace=setting('SUPABASE_NAMESPACE','sumai-compass')
        try:
            u=urlparse(self.url)
            okay=(u.scheme=='https' and u.hostname and u.hostname.endswith('.supabase.co')
                  and not u.username and not u.password and u.port in (None,443)
                  and u.path in ('','/') and not u.query and not u.fragment)
        except ValueError: okay=False
        if not okay or not self.key: raise AppError('「初期設定」の接続情報をStreamlit Secretsに設定してください。')
        if not re.fullmatch(r'[\w-]{1,80}',self.namespace,flags=re.ASCII): raise AppError('保存領域名の形式を確認してください。')
        self.headers={'apikey':self.key,'Content-Type':'application/json','Accept':'application/json'}
        if not self.key.startswith('sb_secret_'):
            try:
                parts=self.key.split('.')
                payload=json.loads(base64.urlsafe_b64decode(parts[1]+'='*(-len(parts[1])%4)))
                if len(parts)!=3 or payload.get('role')!='service_role': raise ValueError()
            except Exception: raise AppError('Secret keyまたはservice_roleキーを設定してください。') from None
            self.headers['Authorization']='Bearer '+self.key
        self.session=requests.Session();self.address_points=saved_position_memory()
    def call(self,method,table,params=None,data=None,prefer=None):
        if table not in (UNIT_TABLE,SEARCH_TABLE,PLACE_TABLE): raise AppError('保存先が不正です。')
        headers=dict(self.headers)
        if prefer: headers['Prefer']=prefer
        try:
            response=self.session.request(method,self.url+'/rest/v1/'+table,params=params,json=data,
                                          headers=headers,timeout=(4,15),allow_redirects=False)
        except requests.RequestException: raise AppError('Supabaseに接続できません。検索結果は画面に保持されます。') from None
        if getattr(self,'audit',None) and not getattr(self,'_saving_audit',False):
            self.audit.add('database','db_response','INFO' if 200<=response.status_code<300 else 'ERROR',{'method':method,'table':table,'status':response.status_code,'bytes':len(response.content)})
        if response.status_code in (401,403): raise AppError('SupabaseのSecret key・権限を確認してください。')
        if response.status_code==404: raise AppError('新しい保存テーブルがありません。「初期設定」のSQLを実行してください。')
        if not 200<=response.status_code<300: raise AppError(f'Supabase処理失敗（HTTP {response.status_code}）。「初期設定」のSQL・接続先を確認してください。')
        if not response.content.strip(): return None
        try: return response.json()
        except ValueError: raise AppError('Supabaseからの応答を確認できません。') from None
    def check(self):
        for table,select in ((UNIT_TABLE,'key,latitude,longitude,layout,rent,fees,area,age,structure'),
                             (SEARCH_TABLE,'id,status,conditions,summary'), (PLACE_TABLE,'key,title,kind,latitude,longitude')):
            rows=self.call('GET',table,{'namespace':'eq.'+self.namespace,'select':select,'limit':1})
            if not isinstance(rows,list): raise AppError('保存テーブルの応答形式が不正です。')
    def acquisition_payload(self,rows):
        unique={}
        for row in rows:
            property_id=row.get('property_id')
            if not property_id:continue
            stamp=row.get('fetched_at') or utc_now()
            try:
                parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if parsed.tzinfo is None:raise ValueError()
                stamp=parsed.astimezone(timezone.utc).isoformat()
            except (ValueError,TypeError,AttributeError):raise AppError('物件IDの取得日時を確認できません。') from None
            if property_id in unique and unique[property_id]['finished_at']>=stamp:continue
            unique[property_id]={'namespace':self.namespace,'id':'acquired.'+hashlib.sha256(property_id.encode()).hexdigest(),
                'status':'rental_acquisition','started_at':stamp,'finished_at':stamp,
                'conditions':{'provider':'SUUMO','property_id':property_id,'schema':2},
                'summary':{'last_success_at':stamp,'listing_key':compact_listing_key(row)}}
        return list(unique.values())

    def save_units(self,rows):
        address_points={}
        for r in rows:
            address=normal(r.get('address') or r.get('inferred_address')) if isinstance(r,dict) else ''
            if address and isinstance(r,dict) and has_point(r) and r.get('coordinate_precision') not in ('town','unknown'):
                address_points[address]=(float(r['latitude']),float(r['longitude']),normal(r.get('map_address') or address))
        unique={}
        for r in rows:
            compact=compact_saved_listing(r)
            if compact:
                key=compact_listing_key(compact);unique[key]=newest_observation(unique.get(key),compact)
        saved=set();items=list(unique.items());self.last_save_stats={'new':0,'updated':0,'failed':0}
        if not hasattr(self,'job_new_keys'):self.job_new_keys=set()
        if not hasattr(self,'covered_acquisitions'):self.covered_acquisitions={}
        for offset in range(0,len(items),200):
            batch=items[offset:offset+200]
            unknown=[key for key,compact in batch if not compact.get('fetched_at')]
            if unknown:
                previous=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'status':'eq.rental_listing',
                    'select':'id,summary','id':'in.('+','.join('listing.'+key for key in unknown)+')','limit':200})
                if not isinstance(previous,list):raise AppError('以前の取得日時を確認できません。')
                stamps={str(r.get('id','')).removeprefix('listing.'):((r.get('summary') or {}).get('listing') or {}).get('fetched_at') for r in previous}
                for key,compact in batch:
                    if not compact.get('fetched_at') and stamps.get(key):compact['fetched_at']=stamps[key]
            payload=[dict(namespace=self.namespace,id='listing.'+key,status='rental_listing',started_at=utc_now(),finished_at=None,
                conditions={'record_type':'rental_listing','schema':6,'fields':['property_id','rent','layout','address','fetched_at'],
                            'address_origin':'suumo_room_marker_to_gsi_residential_address;homes_map_image'},summary={'listing':compact}) for key,compact in batch]
            result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},payload,'resolution=ignore-duplicates,return=representation')
            if not isinstance(result,list):raise AppError('Supabaseから新規保存の確認が得られません。')
            inserted={str(r.get('id','')).removeprefix('listing.') for r in result if isinstance(r,dict) and r.get('id')}
            self.job_new_keys.update(inserted)
            updates=[r for r in payload if r['id'].removeprefix('listing.') not in inserted]
            ledger=self.acquisition_payload([compact for _,compact in batch])
            rest=updates+ledger
            if rest:
                response=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},rest,'resolution=merge-duplicates,return=representation')
                returned={r.get('id') for r in response if isinstance(r,dict)} if isinstance(response,list) else set()
                if not {r['id'] for r in rest}<=returned:raise AppError('募集情報の更新・取得済みIDの保存を確認できません。')
            for row in ledger:self.covered_acquisitions[row['conditions']['property_id']]=(row['finished_at'],row['summary']['listing_key'])
            saved.update(key for key,_ in batch)
            self.last_save_stats['new']+=len(inserted);self.last_save_stats['updated']+=len(updates)
        if not set(unique)<=saved:raise AppError('Supabaseから募集情報の保存確認が得られません。')
        if address_points:
            try:self.save_address_points(address_points)
            except AppError as exc:self.last_save_stats['address_cache_error']=str(exc)
        return saved
    def save_search(self,search):
        result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},
                         [dict(search,namespace=self.namespace)],'resolution=merge-duplicates,return=representation')
        if not isinstance(result,list) or not any(x.get('id')==search['id'] for x in result if isinstance(x,dict)):
            raise AppError('検索履歴の保存を確認できません。')
    def save_acquisition_ids(self,rows):
        payload=self.acquisition_payload(rows)
        covered=getattr(self,'covered_acquisitions',{})
        payload=[r for r in payload if covered.get(r['conditions']['property_id'])!=(r['finished_at'],r['summary']['listing_key'])]
        if not payload:return
        result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},payload,'resolution=merge-duplicates,return=representation')
        returned={r.get('id') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
        if not {r['id'] for r in payload}<=returned:raise AppError('物件IDの取得済み管理情報を保存できません。')

    def load_recent_acquisition_ids(self):
        """Load the recent SUUMO acquisition ledger without rereading every saved listing.

        rental_acquisition rows are written only after the corresponding compact listing
        save succeeds. The ledger itself is therefore sufficient for the 3-month skip
        decision; loading housing_searches_v1 rental_listing rows here was redundant and
        became increasingly expensive as the stored inventory grew.
        """
        cached=getattr(self,'acquisition_cache',None)
        if cached is not None and time.monotonic()-getattr(self,'acquisition_cache_at',0)<900:return cached
        out={};offset=0;cutoff=acquisition_cutoff().isoformat(timespec='seconds');pages=0;raw=0
        while True:
            rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'status':'eq.rental_acquisition',
                'finished_at':'gte.'+cutoff,'select':'conditions,summary,finished_at','order':'id.asc','limit':1000,'offset':offset})
            if not isinstance(rows,list):raise AppError('過去3か月の取得済み物件IDを確認できません。')
            pages+=1;raw+=len(rows)
            for row in rows:
                conditions=row.get('conditions') or {};summary=row.get('summary') or {}
                property_id=conditions.get('property_id');stamp=summary.get('last_success_at') or row.get('finished_at')
                if not property_id or not re.fullmatch(r'SUUMO:(?:bc|jnc):\d+',str(property_id)):continue
                if not recent_acquisition(stamp):continue
                previous=out.get(property_id)
                if previous is None or str(stamp)>str(previous):out[property_id]=stamp
            if len(rows)<1000:break
            offset+=len(rows)
        self.last_acquisition_load_stats={'pages':pages,'raw_records':raw,'recent_ids':len(out),'full_listing_scan':False}
        self.acquisition_cache=out;self.acquisition_cache_at=time.monotonic()
        return out

    def load_units(self,bounds=None,layouts=None,on_progress=None,cancel_event=None):
        out=[];offset=0
        self.last_load_diagnostic={'time':utc_now(),'bounds':list(bounds) if bounds else None,'layouts':list(layouts) if layouts is not None else None,
                                   'pages':[],'excluded':{'invalid':0,'layout':0,'legacy_schema':0},'raw_records':0,'storage_schema':6,'geocode_on_load':True}
        while True:
            if cancel_event is not None and cancel_event.is_set():raise SearchCancelled()
            rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'select':'summary,conditions','status':'eq.rental_listing','order':'id.asc','limit':1000,'offset':offset})
            if not isinstance(rows,list):raise AppError('保存した募集情報を読み取れません。')
            self.last_load_diagnostic['pages'].append({'table':SEARCH_TABLE,'offset':offset,'returned':len(rows),'server_filters':['namespace','rental_listing']})
            self.last_load_diagnostic['raw_records']+=len(rows)
            if not rows:break
            page_start=len(out)
            for item in rows:
                meta=item.get('conditions') or {}
                if int(meta.get('schema') or 0) not in (4,5,6):
                    self.last_load_diagnostic['excluded']['legacy_schema']+=1;continue
                raw=(item.get('summary') or {}).get('listing')
                compact=compact_saved_listing(raw if isinstance(raw,dict) else {})
                if not compact:self.last_load_diagnostic['excluded']['invalid']+=1;continue
                if layouts is not None and compact['layout'] not in layouts:self.last_load_diagnostic['excluded']['layout']+=1;continue
                key=compact_listing_key(compact)
                out.append({**compact,'key':key,'title':compact['address'],'rent':compact['rent'],'layout':compact['layout'],'address':compact['address'],
                            'fetched_at':compact.get('fetched_at'),'fees':0,'loaded_from_compact_storage':True})
            offset+=len(rows)
            if on_progress:on_progress(out[page_start:],dict(self.last_load_diagnostic))
        # Do not count legacy copies again when an ID record covers that observation.
        represented={hashlib.sha256(json.dumps({k:r[k] for k in ('rent','layout','address')},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for r in out if r.get('property_id')}
        out=[r for r in out if r.get('property_id') or r['key'] not in represented]
        unique=list({r['key']:r for r in out}.values())
        self.last_load_diagnostic.update(before_key_merge=len(out),key_duplicates=len(out)-len(unique),returned=len(unique))
        return unique
    def save_address_points(self,points):
        """Persist address -> coordinate mappings so saved-listing loads do not geocode again."""
        if not isinstance(points,dict) or not points:return 0
        payload=[]
        for address,point in points.items():
            address=normal(address);key=saved_address_point_key(address)
            if not key or not point:continue
            try:
                lat,lng=float(point[0]),float(point[1])
            except (ValueError,TypeError,IndexError):continue
            if not has_point({'latitude':lat,'longitude':lng}):continue
            payload.append(dict(namespace=self.namespace,key=key,title=address[:220],kind=ADDRESS_POINT_KIND,
                                latitude=lat,longitude=lng,fetched_at=utc_now()))
        saved=0
        for offset in range(0,len(payload),500):
            batch=payload[offset:offset+500]
            result=self.call('POST',PLACE_TABLE,{'on_conflict':'namespace,key','select':'key'},batch,
                             'resolution=merge-duplicates,return=representation')
            returned={r.get('key') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
            expected={r['key'] for r in batch}
            if not expected<=returned:raise AppError('住所座標キャッシュの保存を確認できません。')
            saved+=len(expected)
        return saved

    def load_address_points(self,addresses,cancel_event=None):
        """Load persistent address coordinates in pages and return only requested addresses."""
        wanted={saved_address_point_key(a):normal(a) for a in addresses if saved_address_point_key(a)}
        if not wanted:return {}
        out={};offset=0
        while True:
            if cancel_event is not None and cancel_event.is_set():raise SearchCancelled()
            rows=self.call('GET',PLACE_TABLE,{'namespace':'eq.'+self.namespace,'kind':'eq.'+ADDRESS_POINT_KIND,
                'select':'key,title,latitude,longitude','order':'key.asc','limit':1000,'offset':offset})
            if not isinstance(rows,list):raise AppError('住所座標キャッシュを読み取れません。')
            for row in rows:
                key=str(row.get('key') or '')
                address=wanted.get(key)
                if not address:continue
                try:point=(float(row['latitude']),float(row['longitude']),address)
                except (KeyError,ValueError,TypeError):continue
                if has_point({'latitude':point[0],'longitude':point[1]}):out[address]=point
            if len(out)>=len(wanted) or len(rows)<1000:break
            offset+=len(rows)
        return out

    def save_places(self,rows,kind):
        if not rows: return set()
        payload=[dict(namespace=self.namespace,key=hashlib.sha256((kind+r['name']+str(r['lat'])+str(r['lng'])).encode()).hexdigest(),
                      title=r['name'],kind=kind,latitude=r['lat'],longitude=r['lng'],fetched_at=utc_now()) for r in rows]
        result=self.call('POST',PLACE_TABLE,{'on_conflict':'namespace,key','select':'key'},payload,
                         'resolution=merge-duplicates,return=representation')
        expected={r['key'] for r in payload}
        returned={r.get('key') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
        if not expected<=returned: raise AppError('周辺施設の保存を確認できません。')
        return expected
    def load_places(self,center,kind):
        s,w,n,e=rectangle(center,1000);offset=0;out=[]
        while True:
            rows=self.call('GET',PLACE_TABLE,{'namespace':'eq.'+self.namespace,'kind':'eq.'+kind,'select':'*','limit':300,
                'offset':offset,'order':'key.asc','and':f'(latitude.gte.{s},latitude.lte.{n},longitude.gte.{w},longitude.lte.{e})'})
            if not isinstance(rows,list): raise AppError('周辺施設を読み取れません。')
            if not rows: break
            for r in rows:
                if meters(center,(r['latitude'],r['longitude']))<=1000:
                    out.append(dict(name=r['title'],lat=r['latitude'],lng=r['longitude']))
            offset+=len(rows)
        return out
    def history(self):
        rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'select':'*','order':'started_at.desc','limit':20,'status':'in.(started,completed,partial,failed,cancelled)'})
        if not isinstance(rows,list): raise AppError('検索履歴を読み取れません。')
        return rows

    def load_diagnostics(self,search_id):
        if not re.fullmatch(r'[0-9a-f]{64}',search_id): raise AppError('検索IDを確認してください。')
        events=[];offset=0
        while True:
            rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'select':'id,summary','id':'like.'+search_id+'.log.*',
                                              'status':'eq.diagnostic_log','order':'id.asc','limit':300,'offset':offset})
            if not isinstance(rows,list):raise AppError('保存ログの形式を確認できません。')
            if not rows:break
            for row in rows:events.extend(row.get('summary',{}).get('events',[]))
            offset+=len(rows)
        return events


GEO_HTTP_CACHE={}
GEO_EMPTY_ADDRESS_TILES={}
GEO_HTTP_LOCK=threading.Lock()


def response_title(text):
    match=re.search(r'<title\b[^>]*>(.*?)</title\s*>',(text or '')[:65536],re.I|re.S)
    return normal(html.unescape(re.sub(r'<[^>]+>',' ',match.group(1))))[:160] if match else ''

def response_encoding(response):
    content_type=response.headers.get('Content-Type','')
    charset=re.search(r'charset\s*=\s*[\"\']?([\w.-]+)',content_type,re.I)
    meta=re.search(br'charset\s*=\s*[\"\']?([\w.-]+)',response.content[:8192],re.I) if not charset else None
    candidate=charset.group(1) if charset else meta.group(1).decode('ascii') if meta else None
    if candidate:
        try:response.content.decode(candidate);return candidate
        except (LookupError,UnicodeError):pass
    try:response.content.decode('utf-8');return 'utf-8'
    except UnicodeError:return response.apparent_encoding or 'utf-8'


class PublicWeb:
    def __init__(self):
        self.address_points=None
        self.local=threading.local();self.cancel_event=threading.Event();self.cache_lock=threading.RLock();self.http_flights={};self.host_slots={};self.detail_slots=threading.BoundedSemaphore(DETAIL_WORKERS);self.jhj_tile_cache={};self.jhj_tile_cached_at={};self.address_result_cache={}
        self.headers={'User-Agent':'SumaiCompassRebuild/1.0 (personal rental research)', 'Accept-Language':'ja'}
        self.host_gates={};self.host_last_request={};self.host_backoff={};self.route_cooldowns={};self.session_primed=threading.local();self.proxy_routes=[];self.route_preferred={};self.route_lock=threading.Lock();self.http_cache={};self.headers.update({'Accept':'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8'})
        self.robots={};self.lock=threading.Lock();self.layout_form_lock=threading.Lock();self.layout_form_cache={};self.provider_cache={};self.provider_lock=threading.RLock();self.unavailable_hosts={};self.failed_detail_urls={}
    def configure(self,config):
        self.proxy_routes=list((config or {}).get('proxies',[]))
        trace(self,'transport_config',{'proxy_count':len(self.proxy_routes),'rental_route':'proxy' if self.proxy_routes else 'direct','other_services':'direct'},stage='http_config')
    def session(self,route=None):
        if not hasattr(self.local,'sessions'):self.local.sessions={}
        if route is None and hasattr(self.local,'session'):return self.local.session
        if route not in self.local.sessions:self.local.sessions[route]=requests.Session()
        return self.local.sessions[route]
    def check_cancel(self):
        if self.cancel_event.is_set():raise SearchCancelled()
    def pause(self,seconds):
        if self.cancel_event.wait(max(0.,seconds)):raise SearchCancelled()
    def host_policy(self,host):
        if self.address_points and host and host.endswith('.gsi.go.jp'):return self.address_points['http']
        return {'gates':self.host_gates,'slots':self.host_slots,'starts':self.host_last_request,'backoff':self.host_backoff,'lock':self.route_lock}
    def route_request(self,session,method,url,options):
        self.check_cancel();host=urlparse(url).hostname
        headers=dict(self.headers);headers.update(options.get('headers',{}));options={k:v for k,v in options.items() if k!='headers'}
        policy=self.host_policy(host)
        with policy['lock']:
            gate=policy['gates'].setdefault(host,threading.Lock())
            slots=policy['slots'].setdefault(host,threading.BoundedSemaphore(PER_HOST_SLOTS))
        while not slots.acquire(timeout=.1):self.check_cancel()
        try:
            while not gate.acquire(timeout=.1):self.check_cancel()
            try:
                interval=PER_HOST_MIN_INTERVAL;parser=self.robots.get(urlparse(url).scheme+'://'+urlparse(url).netloc)
                if parser:
                    crawl=parser.crawl_delay(self.headers['User-Agent']);rate=parser.request_rate(self.headers['User-Agent'])
                    if crawl:interval=max(interval,float(crawl))
                    if rate and rate.requests:interval=max(interval,float(rate.seconds)/rate.requests)
                while True:
                    delay=max(0.,policy['starts'].get(host,0.)+interval-time.monotonic(),policy['backoff'].get(host,0.)-time.monotonic())
                    if delay<=0:break
                    self.pause(delay)
                self.check_cancel();policy['starts'][host]=time.monotonic()
            finally:gate.release()
            # Keep one-second start spacing, but do not serialize the response wait.
            return session.request(method,url,headers=headers,timeout=(4,12),allow_redirects=False,**options)
        finally:slots.release()
    def response_info(self,response):
        title=response_title(response.text) if 'html' in response.headers.get('Content-Type','').lower() else ''
        challenge=bool(re.search(r'^(?:just a moment|access denied|attention required|403 forbidden|verify (?:you|your)|security check)',title,re.I))
        return {'page_title':title,'challenge_page':challenge,'response_sha256':hashlib.sha256(response.content).hexdigest(),
                'response_headers':{k:response.headers.get(k,'') for k in ('Server','Content-Type','Retry-After','Via','X-Cache','CF-Ray')}}
    def prime_session(self,session,root,options,route_number):
        if not hasattr(self.session_primed,'keys'):self.session_primed.keys=set()
        if not hasattr(self.session_primed,'references'):self.session_primed.references={}
        key=(root,route_number)
        if key in self.session_primed.keys:return False
        parser=self.robots.get(root)
        if parser is None or not parser.can_fetch(self.headers['User-Agent'],root+'/'):return False
        self.session_primed.keys.add(key)
        # Enter the site's published home page using the same route and cookie jar.
        warm_options={k:v for k,v in options.items() if k in ('proxies',)}
        reply=self.route_request(session,'GET',root+'/',warm_options)
        info=self.response_info(reply)
        trace(self,'session_entry',{'entry_url':root+'/','route_number':route_number,'status':reply.status_code,
                                  'cookie_count':len(getattr(session,'cookies',[])),**info},stage='transport')
        okay=reply.status_code==200 and bool(reply.content.strip()) and not info['challenge_page']
        if okay:self.session_primed.references[key]=root+'/'
        return okay
    def request_routes(self,method,url,**kwargs):
        u=urlparse(url);host=u.hostname;root=u.scheme+'://'+u.netloc
        rental=host in RENTAL_HOSTS
        routes=self.proxy_routes if rental and self.proxy_routes else [None]
        with self.route_lock:preferred=self.route_preferred.get(host,0)%len(routes)
        ordered=list(range(preferred,len(routes)))+list(range(preferred))
        last=None;skipped=[]
        for index in ordered:
            self.check_cancel()
            with self.route_lock:until=max(self.route_cooldowns.get((host,index),0.),self.route_cooldowns.get((host,index,u.path),0.))
            if until>time.monotonic():
                skipped.append(index+1);trace(self,'route_cooldown',{'host':host,'route_number':index+1,'remaining_seconds':round(until-time.monotonic())},stage='transport');continue
            route=routes[index];session=self.session(route);options=dict(kwargs)
            if route:options['proxies']={'http':route,'https':route}
            referer=getattr(self.session_primed,'references',{}).get((root,index+1))
            if referer:options['headers']={**options.get('headers',{}),'Referer':referer}
            for attempt in range(3):
                try:
                    response=self.route_request(session,method,url,options);status=response.status_code;info=self.response_info(response)
                    trace(self,'transport_attempt',{'url':url,'method':method,'route':'proxy' if route else 'direct','route_number':index+1 if route else None,'attempt':attempt+1,'status':status,'bytes':len(response.content),**info},stage='transport')
                    last=response
                    blocked=status==403 or info['challenge_page']
                    if rental and blocked and attempt==0 and u.path not in ('/','/robots.txt'):
                        if self.prime_session(session,root,options,index+1):
                            headers=dict(options.get('headers',{}));headers['Referer']=root+'/';options['headers']=headers
                            trace(self,'session_retry',{'url':url,'route_number':index+1,'change':'same-route public entry cookies and actual Referer'},stage='transport');continue
                    retry=status in (429,500,502,503,504)
                    if retry:
                        raw=response.headers.get('Retry-After','')
                        try:delay=max(0.,float(raw))
                        except (ValueError,TypeError):
                            try:delay=max(0.,(parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds())
                            except (ValueError,TypeError,OverflowError):delay=2**attempt
                        policy=self.host_policy(host)
                        with policy['lock']:policy['backoff'][host]=max(policy['backoff'].get(host,0.),time.monotonic()+delay)
                        if delay>30:
                            with self.route_lock:self.route_cooldowns[(host,index)]=time.monotonic()+delay
                            trace(self,'route_cooldown',{'host':host,'route_number':index+1,'remaining_seconds':round(delay),'reason':'Retry-After'},stage='transport');break
                        if attempt<2:
                            trace(self,'http_retry',{'url':url,'status':status,'wait_seconds':delay,'route_number':index+1 if route else None},'WARNING','transport');self.pause(delay);continue
                    if blocked:
                        with self.route_lock:self.route_cooldowns[(host,index,u.path)]=time.monotonic()+60
                        if info['challenge_page'] and status<400:
                            error=AppError(host+'：物件本文ではなくアクセス検証画面が返りました。');error.diagnostic={'status':status,**info};last=error
                        break
                    if status==202 and not response.content.strip() or retry:break
                    with self.route_lock:self.route_preferred[host]=index
                    return response
                except (requests.Timeout,requests.ConnectionError) as exc:
                    trace(self,'proxy_error' if route else 'network',{'url':url,'route_number':index+1 if route else None,'attempt':attempt+1,'exception_type':type(exc).__name__},'WARNING','transport')
                    last=exc
                    if attempt<2:self.pause(2**attempt);continue
                    break
            if len(ordered)>1:trace(self,'proxy_switch',{'url':url,'failed_route_number':index+1,'remaining_routes':len(ordered)-ordered.index(index)-1},'WARNING','transport')
        if isinstance(last,Exception):raise last
        if last is None:
            error=AppError(host+'：取得経路の待機中です（直前の403・429等）。設定した全経路の待機が終わってから再検索してください。');error.diagnostic={'skipped_routes':skipped};raise error
        return last
    def _fetch_uncached(self,url,method='GET',**kwargs):
        allowed=tuple(RENTAL_HOSTS)+('mreversegeocoder.gsi.go.jp','msearch.gsi.go.jp','maps.gsi.go.jp','cyberjapandata.gsi.go.jp','japanese-addresses-v2.geoloniamaps.com','geolonia.github.io','img01.suumo.com','img02.suumo.com','maps.googleapis.com','maps.google.com')
        u=urlparse(url)
        if u.scheme!='https' or u.hostname not in allowed or u.username or u.password: raise AppError('取得先が不正です。')
        if method=='GET' and not kwargs.get('params') and url in self.http_cache:
            trace(self,'cache_hit',{'url':url,'source':'successful_detail_http_cache'},stage='http_cache');return self.http_cache[url]
        if u.hostname in self.unavailable_hosts:raise AppError(self.unavailable_hosts[u.hostname])
        if method=='GET' and not kwargs.get('params') and url in self.failed_detail_urls:raise AppError(self.failed_detail_urls[url])
        try:
            response=self.request_routes(method,url,**kwargs)
            for _ in range(3):
                if response.status_code not in (301,302,303,307,308): break
                target=urljoin(response.url,response.headers.get('Location',''))
                v=urlparse(target)
                if v.scheme!='https' or v.hostname!=u.hostname:
                    raise AppError('取得先が別のサイトに移動しました。')
                response=self.request_routes('GET',target)
            response.raise_for_status()
            if response.status_code==202 and not response.content.strip():
                message=f'{u.hostname}：HTTP 202・本文0バイト。掲載データが届いていません。この検索中は同じ取得元への問い合わせを停止します。'
                self.unavailable_hosts[u.hostname]=message;error=AppError(message);error.diagnostic={'status':202,'bytes':0};raise error
        except requests.Timeout as exc:
            error=AppError(f'{u.hostname}：接続または応答がタイムアウトしました。');error.diagnostic={'exception_type':type(exc).__name__,'reason':'接続経路の通信失敗'};raise error from None
        except requests.HTTPError as exc:
            message=f'{u.hostname}：HTTP {exc.response.status_code}（公開ページの取得失敗）。'
            if method=='GET' and not kwargs.get('params'):self.failed_detail_urls[url]=message
            error=AppError(message);error.diagnostic={**self.response_info(exc.response),'status':exc.response.status_code}
            if u.hostname=='cyberjapandata.gsi.go.jp' and u.path.startswith('/xyz/experimental_jhj/') and exc.response.status_code==404:
                code=re.search(r'<Code>([^<]+)</Code>',exc.response.text[:4096])
                if code:error.diagnostic['server_error_code']=code.group(1)
            raise error from None
        except requests.exceptions.ProxyError:
            raise AppError('プロキシ接続失敗。接続先・ポート・認証情報・HTTPS CONNECT対応を確認してください。') from None
        except requests.ConnectionError as exc:
            error=AppError(f'{u.hostname}：接続できません（DNS・ネットワーク・接続先を確認）。');error.diagnostic={'exception_type':type(exc).__name__,'reason':'接続経路の通信失敗'};raise error from None
        except requests.RequestException: raise AppError(f'{u.hostname}：通信処理に失敗しました。') from None
        response.encoding='utf-8' if 'gsi.go.jp' in u.hostname else response_encoding(response)
        if method=='GET' and not kwargs.get('params') and '/chintai/' in u.path:self.http_cache[url]=response
        return response
    def fetch(self,url,method='GET',**kwargs):
        self.check_cancel()
        if method!='GET':return self._fetch_uncached(url,method,**kwargs)
        params=kwargs.get('params') or {}
        pairs=list(params.items()) if isinstance(params,dict) else list(params)
        key=(url,urlencode(sorted(pairs,key=lambda p:str(p[0])),doseq=True))
        geo=urlparse(url).hostname in ('maps.gsi.go.jp','cyberjapandata.gsi.go.jp','mreversegeocoder.gsi.go.jp')
        if geo:
            with GEO_HTTP_LOCK:shared=GEO_HTTP_CACHE.get(key)
            if shared and time.monotonic()-shared[0]<86400:return shared[1]
        with self.cache_lock:
            if key in self.http_cache:return self.http_cache[key]
            flight=self.http_flights.get(key);owner=flight is None
            if owner:flight=futures.Future();self.http_flights[key]=flight
        if not owner:
            while not flight.done():self.pause(.05)
            return flight.result()
        try:
            result=self._fetch_uncached(url,method,**kwargs)
            with self.cache_lock:self.http_cache[key]=result
            if geo:
                with GEO_HTTP_LOCK:
                    if len(GEO_HTTP_CACHE)>12000:GEO_HTTP_CACHE.clear()
                    GEO_HTTP_CACHE[key]=(time.monotonic(),result)
            flight.set_result(result);return result
        except BaseException as exc:
            flight.set_exception(exc);raise
        finally:
            with self.cache_lock:self.http_flights.pop(key,None)
    def permitted(self,url):
        u=urlparse(url);root=u.scheme+'://'+u.netloc
        with self.lock: parser=self.robots.get(root)
        if parser is None:
            try:
                parser=urllib.robotparser.RobotFileParser();parser.parse(self.fetch(root+'/robots.txt').text.splitlines())
            except AppError as exc:
                if 'HTTP 404' in str(exc): parser=urllib.robotparser.RobotFileParser();parser.parse([])
                else: raise AppError('自動取得ルールを確認できません：'+str(exc)) from None
            with self.lock: self.robots[root]=parser
        permitted=parser.can_fetch(self.headers['User-Agent'],url)
        trace(self,'robots_allowed' if permitted else 'http_403',{'url':url,'rule_source':root+'/robots.txt','permitted':permitted},'INFO' if permitted else 'WARNING','robots')
        return permitted



_original_public_fetch=PublicWeb.fetch
def audited_public_fetch(self,url,method='GET',**kwargs):
    started=time.monotonic()
    details={'url':url,'method':method,'parameters':kwargs.get('params',kwargs.get('data',{}))}
    try:
        cached=method=='GET' and not kwargs.get('params') and url in self.http_cache
        response=_original_public_fetch(self,url,method,**kwargs)
        details.update(status=response.status_code,elapsed_ms=round((time.monotonic()-started)*1000),bytes=len(response.content),
                       content_type=response.headers.get('Content-Type',''),response_url=response.url,
                       response_sha256=hashlib.sha256(response.content).hexdigest())
        if 'html' in details['content_type'].lower():
            details['page_title']=response_title(response.text)
            details['html_selector_counts']='取得先の解析工程で記録（通信ログではHTML全体を再解析しない）'
        trace(self,'cache_hit' if cached else 'http_ok',details,stage='http_cache' if cached else 'http');return response
    except Exception as exc:
        details.update(elapsed_ms=round((time.monotonic()-started)*1000),exception_type=type(exc).__name__,technical_exception=getattr(exc,'diagnostic',{}),message=str(exc) if isinstance(exc,AppError) else '応答処理失敗')
        match=re.search(r'HTTP (\d{3})',details['message']);details['status']=int(match[1]) if match else None
        empty_tile=(urlparse(url).hostname=='cyberjapandata.gsi.go.jp' and urlparse(url).path.startswith('/xyz/experimental_jhj/') and details['technical_exception'].get('status')==404 and details['technical_exception'].get('server_error_code')=='NoSuchKey')
        trace(self,'address_tile_empty_response' if empty_tile else diagnosis_code(details['message']),details,'INFO' if empty_tile else 'ERROR','http');raise
PublicWeb.fetch=audited_public_fetch

_original_db_call=Database.call
def audited_db_call(self,method,table,params=None,data=None,prefer=None):
    audit=getattr(self,'audit',None);started=time.monotonic()
    details={'method':method,'table':table,'row_count':len(data) if isinstance(data,list) else None}
    try:
        result=_original_db_call(self,method,table,params,data,prefer)
        details.update(elapsed_ms=round((time.monotonic()-started)*1000),returned_rows=len(result) if isinstance(result,list) else None)
        if audit and not getattr(self,'_saving_audit',False):audit.add('database','db_ok','INFO',details)
        return result
    except Exception as exc:
        details.update(elapsed_ms=round((time.monotonic()-started)*1000),message=str(exc) if isinstance(exc,AppError) else type(exc).__name__)
        if audit and not getattr(self,'_saving_audit',False):audit.add('database','save','ERROR',details)
        raise
Database.call=audited_db_call

def canonical_provider(provider):
    return PROVIDER_CANONICAL.get(str(provider or ''),str(provider or ''))


def safe_url(url,provider):
    try:
        provider=canonical_provider(provider);u=urlparse(url);host=PROVIDER_HOST.get(provider)
        if not host or u.scheme!='https' or u.hostname!=host or u.username or u.password or u.port not in (None,443):
            return False
        path=u.path or '/'
        prefixes={
            'スマイティ':('/chintai/',),'HOME’S':('/chintai/',),'SUUMO':('/chintai/',),
            'カナリー':('/chintai/',),'アットホーム':('/chintai/',),
            'CHINTAI':('/detail/','/tokyo/','/kanagawa/','/saitama/','/chiba/'),
            'Comfy':('/properties/','/rooms/','/rentals/','/'),
            'アパマンショップ':('/chintai/','/tokyo/','/kanagawa/','/saitama/','/chiba/'),
        }
        return any(path.startswith(p) for p in prefixes.get(provider,()))
    except (ValueError,TypeError): return False


def municipalities(web):
    text=web.fetch('https://maps.gsi.go.jp/js/muni.js').text
    table={}
    for code,value in re.findall(r'(?:GSI\.MUNI_ARRAY|muniArray)\["(\d+)"\]\s*=\s*[\'"]([^\'";]+)',text):
        fields=value.split(',')
        if len(fields)>=4: table[code]=(fields[0],fields[1],fields[3])
    if not table: raise AppError('国土地理院の地域一覧を読み取れません。')
    return table


def reverse(web,point,munis,force=False):
    if getattr(web,'reverse_unavailable',False) and not force: return None
    data=web.fetch('https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress',
                   params={'lat':point[0],'lon':point[1]}).json().get('results',{})
    code=str(data.get('muniCd',''));town=str(data.get('lv01Nm',''))
    trace(web,'reverse_result' if code and town and code in munis else 'parse',{'point':point,'municipality_code':code,'town':town,'known_municipality':code in munis},level='INFO' if code and town and code in munis else 'WARNING',stage='geography')
    if not code or not town or code not in munis: return None
    pref,prefecture,city=munis[code]
    return dict(code=code,pref=pref,town=town,label=prefecture+city+town,point=point)


def _tile_xy(point,z=18):
    lat,lng=map(float,point);n=2**z
    x=int((lng+180.0)/360.0*n)
    y=int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
    return x,y


def _property_value(props,exact=(),contains=()):
    if not isinstance(props,dict):return ''
    normalized={normal(k).replace(' ','').replace('　',''):v for k,v in props.items()}
    for key in exact:
        nk=normal(key).replace(' ','').replace('　','')
        if nk in normalized and normalized[nk] not in (None,''):return normal(normalized[nk])
    for nk,value in normalized.items():
        if value in (None,''):continue
        if any(token in nk for token in contains):return normal(value)
    return ''


def _cache_jhj_features(web,key,features,cached_at=None):
    with web.cache_lock:
        cache=web.jhj_tile_cache;times=web.jhj_tile_cached_at
        if key not in cache and len(cache)>=512:
            oldest=next(iter(cache));cache.pop(oldest,None);times.pop(oldest,None)
        cache[key]=features;times[key]=time.monotonic() if cached_at is None else cached_at


def _jhj_tile(web,x,y,z=18):
    """Fetch GSI residential-address points once per tile. Images are never persisted."""
    cache=getattr(web,'jhj_tile_cache',None)
    if cache is None:cache={};web.jhj_tile_cache=cache
    key=(z,x,y)
    with web.cache_lock:
        at=getattr(web,'jhj_tile_cached_at',{}).get(key,0)
        if key in cache and time.monotonic()-at<(3600 if not cache[key] else 86400):return cache[key]
        cache.pop(key,None)
    with GEO_HTTP_LOCK:empty_at=GEO_EMPTY_ADDRESS_TILES.get(key)
    if empty_at is not None and time.monotonic()-empty_at<3600:
        _cache_jhj_features(web,key,[],empty_at);trace(web,'address_tile_empty',{'tile':list(key),'source':'cached_NoSuchKey_404'},stage='address.tiles');return []
    url=f'https://cyberjapandata.gsi.go.jp/xyz/experimental_jhj/{z}/{x}/{y}.geojson'
    try:
        data=web.fetch(url).json()
        if not isinstance(data,dict) or data.get('type')!='FeatureCollection' or not isinstance(data.get('features'),list):raise AppError('住所タイルが有効なGeoJSON FeatureCollectionではありません。')
        features=data['features']
    except (AppError,ValueError,TypeError) as exc:
        diagnostic=getattr(exc,'diagnostic',{})
        if diagnostic.get('status')==404 and diagnostic.get('server_error_code')=='NoSuchKey':
            _cache_jhj_features(web,key,[])
            with GEO_HTTP_LOCK:
                if len(GEO_EMPTY_ADDRESS_TILES)>12000:GEO_EMPTY_ADDRESS_TILES.clear()
                GEO_EMPTY_ADDRESS_TILES[key]=time.monotonic()
            trace(web,'address_tile_empty',{'url':url,'tile':list(key),'status':404,'server_error_code':'NoSuchKey','reason':'この住所タイルのオブジェクトが存在しないため空タイルとして扱う'},stage='address.tiles')
            return []
        trace(web,'address_tile_failed',{'url':url,'exception_type':type(exc).__name__,'reason':str(exc),'diagnostic':getattr(exc,'diagnostic',{})},'WARNING','address.tiles')
        raise
    _cache_jhj_features(web,key,features)
    return features


def map_point_to_residential_address(web,point,munis,max_distance_m=90,expected_code=None,expected_towns=None):
    expected_towns=tuple(normal(x) for x in (expected_towns or ()) if normal(x))
    key=(round(float(point[0]),6),round(float(point[1]),6),int(max_distance_m),str(expected_code or ''),tuple(address_key(x) for x in expected_towns))
    with web.cache_lock:
        cached=web.address_result_cache.get(key)
    if cached:
        age=time.monotonic()-cached[0];value=cached[1];ttl=86400 if value else ADDRESS_NEGATIVE_CACHE_SECONDS
        if age<ttl:
            trace(web,'address_cached' if value else 'address_negative_cached',{'point':list(point),'address':value.get('address') if value else None,'age_seconds':round(age,1)},stage='location')
            return dict(value) if value else None
    result=_map_point_to_residential_address(web,point,munis,max_distance_m,expected_code,expected_towns)
    reference=result.get('reference_point') if result else None
    if result and reference:remember_saved_position(web.address_points,result['address'],(*reference,result['address']))
    with web.cache_lock:
        cache=web.address_result_cache
        if len(cache)>=12000:cache.pop(next(iter(cache)))
        cache[key]=(time.monotonic(),dict(result) if result else None)
    return result


def _map_point_to_residential_address(web,point,munis,max_distance_m=90,expected_code=None,expected_towns=()):
    """Map pin -> nearest GSI residential-address point with a verified town.

    Fast path: the provider search already supplies a municipality/town constraint. We
    compare the nearest JHJ residential point against that constraint and avoid a
    separate reverse-geocoder request when they agree. Boundary/ambiguous cases fall
    back to the reverse geocoder before acceptance, preserving the stricter check.
    """
    x,y=_tile_xy(point,18);candidates=[];tile_errors=[];feature_count=0
    z=18;n=2**z
    fx=(float(point[1])+180)/360*n
    fy=(1-math.asinh(math.tan(math.radians(float(point[0]))))/math.pi)/2*n
    tile_m=40075016.686*math.cos(math.radians(float(point[0])))/n
    tiles=[]
    for dx in (-1,0,1):
        for dy in (-1,0,1):
            tx,ty=x+dx,y+dy
            gx=max(tx-fx,0.,fx-(tx+1));gy=max(ty-fy,0.,fy-(ty+1))
            if math.hypot(gx,gy)*tile_m<=max_distance_m+2:tiles.append((tx,ty))
    def read_tile(tile):return _jhj_tile(web,*tile,18)
    with web.cache_lock:
        tile_cache=web.jhj_tile_cache;tile_times=web.jhj_tile_cached_at;now=time.monotonic()
        cached_tiles=[(t,tile_cache[(18,*t)],None) for t in tiles if (18,*t) in tile_cache and now-tile_times.get((18,*t),0)<(3600 if not tile_cache[(18,*t)] else 86400)]
    tile_results=cached_tiles if len(cached_tiles)==len(tiles) else bounded_results(tiles,read_tile,workers=2,stop_event=web.cancel_event)
    for tile,features,error in tile_results:
        if tile is None:continue
        if error:
            tile_errors.append({'tile':list(tile),'error':str(error),'exception_type':type(error).__name__});continue
        feature_count+=len(features)
        for feature in features:
            if not isinstance(feature,dict):continue
            geo=feature.get('geometry') or {};coords=geo.get('coordinates')
            if geo.get('type')!='Point' or not isinstance(coords,(list,tuple)) or len(coords)<2:continue
            try:lng2,lat2=float(coords[0]),float(coords[1])
            except (ValueError,TypeError):continue
            props=feature.get('properties') or {}
            code=_property_value(props,('市区町村コード','市町村コード','municipality_code','muniCd'),('市区町村コード','市町村コード'))
            if len(code)>5 and code[:5] in munis:code=code[:5]
            town=_property_value(props,('町又は字の名称','町字名','町名'),('町又は字','町字'))
            town=re.sub(r'[-－](\d+)$',r'\1丁目',town)
            block=_property_value(props,('街区符号','街区'),('街区符号',))
            base=_property_value(props,('基礎番号','住居番号'),('基礎番号',))
            if not town or not block or not base:continue
            dist=meters(point,(lat2,lng2))
            candidates.append((dist,code,town,block,base,lat2,lng2,props))
    if tile_errors:
        trace(web,'map_address_unresolved',{'point':list(point),'failed_stage':'address.tiles','reason':'必要な住所タイルの取得に失敗し、最近傍住所を比較できない','tile_errors':tile_errors,'feature_count':feature_count,'requested_tiles':tiles,'candidate_count':len(candidates)},'WARNING','location')
        return None
    if not candidates:
        trace(web,'map_address_unresolved',{'point':list(point),'reason':'GSI住居表示住所の候補なし','tile_errors':tile_errors,'feature_count':feature_count,'requested_tiles':tiles,'maximum_distance_m':max_distance_m},'WARNING','location')
        return None
    best=min(candidates,key=lambda c:c[0])
    if best[0]>max_distance_m:
        trace(web,'map_address_unresolved',{'point':list(point),'reason':'最近傍の住居表示住所が遠すぎる','distance_m':round(best[0],1),'tile_errors':tile_errors,'feature_count':feature_count,'requested_tiles':tiles,'maximum_distance_m':max_distance_m},'WARNING','location')
        return None
    _,code,town,block,base,lat2,lng2,_=best
    expected_code=str(expected_code or '')
    expected_towns=tuple(expected_towns or ())
    expected_match=(not expected_code or code==expected_code) and (not expected_towns or any(town_matches(t,town) or address_key(t)==address_key(town) for t in expected_towns))
    official=None
    if not code or code not in munis or not expected_match:
        # Ambiguous boundary/missing-code cases use the official reverse geocoder as
        # a verification fallback; the common case needs no extra HTTP request.
        official=reverse(web,point,munis,force=True)
        if not official:
            trace(web,'map_address_unresolved',{'point':list(point),'failed_stage':'address.reverse_geocode','reason':'境界確認で有効な市区町村・町丁目を取得できない'},'WARNING','location');return None
        if expected_code and official['code']!=expected_code:
            trace(web,'map_address_unresolved',{'point':list(point),'reason':'物件位置の市区町村が検索対象と一致しない','official_code':official['code'],'expected_code':expected_code},'WARNING','location');return None
        if expected_towns and not any(town_matches(t,official['town']) or address_key(t)==address_key(official['town']) for t in expected_towns):
            trace(web,'map_address_unresolved',{'point':list(point),'reason':'物件位置の町丁目が検索対象と一致しない','official_town':official['town'],'expected_towns':list(expected_towns)},'WARNING','location');return None
        matching=[c for c in candidates if (not c[1] or c[1]==official['code']) and address_key(c[2])==address_key(official['town'])]
        if not matching:
            trace(web,'map_address_unresolved',{'point':list(point),'reason':'逆ジオコーディング町丁目内に住居表示候補なし','town':official['town'],'candidate_count':len(candidates)},'WARNING','location');return None
        best=min(matching,key=lambda c:c[0])
        if best[0]>max_distance_m:return None
        _,code,town,block,base,lat2,lng2,_=best
    if not code and official:code=official['code']
    if not code or code not in munis:
        trace(web,'map_address_unresolved',{'point':list(point),'reason':'住所候補の市区町村コードを確認できない','candidate_code':code},'WARNING','location');return None
    pref_code,prefecture,city=munis[code]
    address=f'{prefecture}{city}{town}{block}-{base}'
    result={'address':address,'distance_m':best[0],'reference_point':[lat2,lng2],
            'official_region':official or {'code':code,'pref':pref_code,'town':town,'label':prefecture+city+town},
            'verification':'jhj_nearest_expected_town' if official is None else 'reverse_geocoder_boundary_fallback'}
    trace(web,'map_address_from_gsi_jhj',{'map_point':list(point),'address':address,'reference_point':[lat2,lng2],'distance_m':round(best[0],1),'verification':result['verification']},stage='location')
    return result


def gsi_regional_tasks(web,bounds,notify):
    """All residential-label points plus an uncapped 100 m grid and edges."""
    munis=municipalities(web);s,w,n,e=bounds
    reverse(web,bounds_center(bounds),munis)
    labels=set();errors=done=0;tiles=tiles_in_bounds(bounds)
    def label_tile(tile):
        x,y=tile
        data=web.fetch(f'https://cyberjapandata.gsi.go.jp/xyz/experimental_nrpt/15/{x}/{y}.geojson').json()
        if not isinstance(data,dict) or not isinstance(data.get('features'),list):
            raise AppError('地名タイルの形式を確認できません。')
        points=[]
        for f in data['features']:
            geo=f.get('geometry') or {}
            if geo.get('type')!='Point': continue
            try:
                lng,lat=map(float,geo['coordinates'][:2])
                if in_rectangle((lat,lng),bounds): points.append((lat,lng))
            except (KeyError,TypeError,ValueError): continue
        return points
    for tile,points,error in bounded_results(tiles,label_tile,workers=12,stop_event=web.cancel_event):
        if tile is not None:
            done+=1
            if error: errors+=1
            else: labels.update(points)
        notify('地名タイル',done,len(tiles),len(labels),errors)
    # Include every edge; no 15x15/sample-count cap. Labels cover small named areas between grid points.
    ny=max(1,math.ceil((n-s)*111320/100))
    nx=max(1,math.ceil((e-w)*111320*math.cos(math.radians((s+n)/2))/100))
    grid=((s+(n-s)*y/ny,w+(e-w)*x/nx) for y in range(ny+1) for x in range(nx+1))
    import itertools
    points=itertools.chain(sorted(labels),grid)
    total=len(labels)+(nx+1)*(ny+1);done=0;regions={}
    def identify(point): return reverse(web,point,munis)
    for point,region,error in bounded_results(points,identify,workers=8,stop_event=web.cancel_event):
        if point is not None:
            done+=1
            if error:
                errors+=1
                if errors>=6: raise AppError('地名判定の接続失敗が続いています：'+str(error))
            elif region: regions[(region['code'],address_key(region['town']))]=region
        notify('地名・丁目判定',done,total,len(regions),errors)
    if not regions: raise AppError('地図範囲の地名を確認できません。国土地理院への接続を確認してください。')
    return sorted(regions.values(),key=lambda r:r['label']),munis,errors



CATALOG_CACHE={}
CATALOG_LOCK=threading.Lock()

def catalog_json(web,url):
    with CATALOG_LOCK: cached=CATALOG_CACHE.get(url)
    if cached and time.monotonic()-cached[0]<86400:
        trace(web,'cache_hit',{'url':url,'age_seconds':round(time.monotonic()-cached[0])},stage='geography');return cached[1]
    try: data=web.fetch(url).json()
    except ValueError: raise AppError('町丁目データのJSONを読み取れません。') from None
    if not isinstance(data,dict) or not isinstance(data.get('data'),list): raise AppError('町丁目データの形式が不正です。')
    with CATALOG_LOCK: CATALOG_CACHE[url]=(time.monotonic(),data)
    return data


def catalog_regions(web,bounds,notify):
    """Independent ABR-derived town catalog; search all towns of overlapping municipalities.
    Representative town points are not boundaries, so neighboring municipalities and
    every town without a point are included rather than imposing a candidate cap.
    """
    root='https://japanese-addresses-v2.geoloniamaps.com/api/ja/'
    index=catalog_json(web,root[:-1]+'.json')
    if not isinstance(index,dict) or not isinstance(index.get('data'),list): raise AppError('町丁目一覧を読み取れません。')
    cities=[];munis={};regions={};errors=[]
    for pref in index['data']:
        pc=str(pref.get('code','')).zfill(6)[:2]
        if pc not in ('11','12','13','14'): continue
        for city in pref.get('cities',[]):
            name=str(city.get('city',''))+str(city.get('ward') or '')
            code=str(city.get('code','')).zfill(6)[:5]
            munis[code]=(pc,pref['pref'],name)
            cities.append((code,pc,pref['pref'],name))
    def towns(city):
        code,pc,pref,name=city
        data=catalog_json(web,root+quote(pref,safe='')+'/'+quote(name,safe='')+'.json')
        if not isinstance(data,dict) or not isinstance(data.get('data'),list): raise AppError(name+'：町丁目一覧の形式が不正です。')
        return data['data']
    done=0;s,w,n,e=bounds
    # Broad buffer protects against town representative points outside the visible boundary.
    padded=(s-.018,w-.023,n+.018,e+.023)
    for city,rows,error in bounded_results(cities,towns,workers=12,stop_event=web.cancel_event):
        if city is not None:
            done+=1
            if error: errors.append(city[3]+'：'+str(error))
            else:
                code,pc,pref,name=city
                nearby=False
                for row in rows:
                    point=row.get('point')
                    if isinstance(point,list) and len(point)>=2 and in_rectangle((point[1],point[0]),padded): nearby=True;break
                if nearby:
                    for row in rows:
                        town=''.join(str(row.get(k) or '') for k in ('oaza_cho','chome','koaza'))
                        if not town: continue
                        point=row.get('point');point=(point[1],point[0]) if isinstance(point,list) and len(point)>=2 else bounds_center(bounds)
                        regions[(code,address_key(town))]=dict(code=code,pref=pc,town=town,label=pref+name+town,point=point)
        notify('独立した町丁目一覧',done,len(cities),len(regions),len(errors))
    if errors: raise AppError('町丁目一覧の一部を取得できません。網羅性を確認できないため検索を開始しません。'+ '／'.join(errors[:3]))
    if not regions: raise AppError('表示範囲と近隣に対応する町丁目がありません。地図範囲を確認してください。')
    return sorted(regions.values(),key=lambda r:r['label']),munis,0


def regional_tasks(web,bounds,notify):
    reasons=[]
    try: return gsi_regional_tasks(web,bounds,notify)
    except Exception as exc:
        reasons.append(str(exc) if isinstance(exc,AppError) else '国土地理院の応答形式を確認できません。')
        web.reverse_unavailable=True
        notify('地名取得先を切替：'+reasons[-1],0,1,0,1)
    try: return catalog_regions(web,bounds,notify)
    except Exception as exc:
        reasons.append(str(exc) if isinstance(exc,AppError) else '独立した町丁目一覧の応答形式を確認できません。')
        raise AppError('地名取得の両経路が失敗しました。'+'／'.join(reasons)) from None

def labeled(soup,labels):
    for label in soup.find_all(['th','dt']):
        if normal(label.get_text(' ',strip=True)).replace(' ','') in labels:
            value=label.find_next_sibling(['td','dd'])
            if value: return value.get_text(' ',strip=True)
    return ''


def json_nodes(value):
    if isinstance(value,dict):
        yield value
        for x in value.values():
            if isinstance(x,(dict,list)): yield from json_nodes(x)
    elif isinstance(value,list):
        for x in value: yield from json_nodes(x)


def coordinate(soup,text,hint=None):
    candidates=[]
    def add(lat,lng,method,priority):
        try:
            lat,lng=float(lat),float(lng)
            if math.isfinite(lat) and math.isfinite(lng) and 34<=lat<=37 and 138<=lng<=141:
                item=(lat,lng,method,priority)
                if item not in candidates:candidates.append(item)
        except (ValueError,TypeError):pass
    pair=r'(3[4-7]\.\d+)\s*[,;| ]\s*(1(?:3[89]|40)\.\d+)'
    for script in soup.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.get_text())
        except ValueError:continue
        for node in json_nodes(data):
            types=node.get('@type',[]);types=[types] if isinstance(types,str) else types
            if any(t in ('Organization','RealEstateAgent','LocalBusiness','Place','Store') for t in types):continue
            geo=node.get('geo')
            if isinstance(geo,dict):add(geo.get('latitude'),geo.get('longitude'),'掲載物件JSON-LD',6)
    regex_text=re.sub(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>.*?</script>', '',text,flags=re.I|re.S)
    # Handle quoted and unquoted JS fields, property-prefixed names, and both pair orders.
    la=r'(?:bukken|property|building|map|center)?(?:lat|latitude|ido)'
    lo=r'(?:bukken|property|building|map|center)?(?:lng|lon|longitude|keido)'
    for pattern,reverse_pair in [(rf'(?<!\w)["\']?{la}["\']?\s*[:=]\s*["\']?(3[4-7]\.\d+)["\']?.{{0,160}}?(?<!\w)["\']?{lo}["\']?\s*[:=]\s*["\']?(1(?:3[89]|40)\.\d+)',False),
                                 (rf'(?<!\w)["\']?{lo}["\']?\s*[:=]\s*["\']?(1(?:3[89]|40)\.\d+)["\']?.{{0,160}}?(?<!\w)["\']?{la}["\']?\s*[:=]\s*["\']?(3[4-7]\.\d+)',True)]:
        for match in re.finditer(pattern,html.unescape(regex_text),re.S|re.I):
            names=re.findall(r'(?<!\w)["\']?('+la+'|'+lo+r')["\']?\s*[:=]',match[0],re.I)
            explicit=len(names)>=2 and all(re.match(r'(?:bukken|property|building)',n,re.I) for n in (names[0],names[-1]))
            add(match[2] if reverse_pair else match[1],match[1] if reverse_pair else match[2],'掲載物件の座標設定' if explicit else '地図中心候補（未検証）',5 if explicit else 1)
    for tag in soup.select('input[value],[data-lat],[data-latitude],[data-ido]'):
        attrs={str(k).lower():v for k,v in tag.attrs.items()}
        for a,b in [('data-lat','data-lng'),('data-lat','data-lon'),('data-latitude','data-longitude'),('data-ido','data-keido')]:
            if a in attrs and b in attrs:add(attrs[a],attrs[b],'掲載地図属性',5 if re.search(r'bukken|property|building',str(tag.attrs),re.I) else 1)
        name=str(tag.get('name') or tag.get('id') or '').lower();value=html.unescape(str(tag.get('value','')))
        if re.search(r'(?:map|gmap|bukken|property|coord|latlng)',name):
            for match in re.finditer(pair,value):add(match[1],match[2],'掲載地図の座標入力',5 if re.search(r'bukken|property|point',name) else 2)
    fields={re.sub(r'[^a-z]','',str(tag.get('name') or tag.get('id') or '').lower()):tag.get('value','') for tag in soup.select('input[value]')}
    for prefix in ('','bukken','property','building','map','center','js'):
        for a,b in [('lat','lng'),('latitude','longitude'),('lat','lon'),('ido','keido')]:
            if prefix+a in fields and prefix+b in fields:add(fields[prefix+a],fields[prefix+b],'掲載地図の座標入力',5 if prefix in ('bukken','property','building') else 2)
    for match in re.finditer(r'(?:LatLng|setView)\s*\(\s*\[?\s*'+pair,text,re.I):add(match[1],match[2],'掲載地図設定（中心・未検証）',1)
    for tag in soup.select('[href],[src],[data-src],[data-url]'):
        for attr in ('href','src','data-src','data-url'):
            url=html.unescape(str(tag.get(attr) or ''));params=parse_qs(urlparse(url).query)
            for key in ('markers','ll','q','query','center'):
                for value in params.get(key,[]):
                    for match in re.finditer(pair,value):add(match[1],match[2],'掲載地図URLの物件ピン' if key=='markers' else '掲載地図URL',5 if key=='markers' and re.search(r'物件|bukken|property|building',str(tag.attrs),re.I) and not re.search(r'店舗|会社|営業|アクセス',str(tag.attrs)) else 1)
            for a,b in [('lat','lng'),('lat','lon'),('latitude','longitude'),('ido','keido')]:
                if a in params and b in params:add(params[a][0],params[b][0],'掲載地図URLの座標（中心・未検証）',1)
            for match in re.finditer(r'!3d(3[4-7]\.\d+)!4d(1(?:3[89]|40)\.\d+)',url):add(match[1],match[2],'埋め込み地図の位置（中心・未検証）',1)
    for match in re.finditer(r'(?:bukken|property|building)(?:Marker|Pin)\s*=\s*(?:L\.)?marker\s*\(\s*\[\s*'+pair,text,re.I):add(match[1],match[2],'物件地図のマーカー座標',5)
    for match in re.finditer(r'(?:bukken|property|building)(?:Marker|Pin)\s*=.{0,120}?position\s*:\s*new\s+(?:google\.maps\.)?LatLng\s*\(\s*'+pair,text,re.I):add(match[1],match[2],'物件地図のマーカー座標',5)
    if hint:add(hint[0],hint[1],'HOME’S物件検索地図',6)
    return candidates


def image_pin_point(content,center,zoom,scale=1):
    """Recognize a colored map pin, then project its tip using declared Web Mercator metadata."""
    import numpy as np
    from PIL import Image
    image=Image.open(io.BytesIO(content)).convert('RGB');pixels=np.asarray(image,dtype=np.int16)
    height,width=pixels.shape[:2];r,g,b=pixels[:,:,0],pixels[:,:,1],pixels[:,:,2]
    mask=(r>150)&(r-g>65)&(r-b>50) # Red location-pin foreground, not black text/gray roads.
    seen=np.zeros(mask.shape,dtype=bool);components=[]
    for y,x in zip(*np.where(mask)):
        if seen[y,x]:continue
        stack=[(int(x),int(y))];seen[y,x]=True;pts=[]
        while stack:
            xx,yy=stack.pop();pts.append((xx,yy))
            for nx,ny in ((xx-1,yy),(xx+1,yy),(xx,yy-1),(xx,yy+1)):
                if 0<=nx<width and 0<=ny<height and mask[ny,nx] and not seen[ny,nx]:seen[ny,nx]=True;stack.append((nx,ny))
        xs,ys=zip(*pts);w,h=max(xs)-min(xs)+1,max(ys)-min(ys)+1
        # A pin must have a head and a tapering lower tip; a red road/label isn't enough.
        if len(pts)<20 or not (5<=w<=80*scale and 8<=h<=100*scale and .8<=h/w<=3.5):continue
        bottom=[xx for xx,yy in pts if yy>=max(ys)-max(1,int(h*.1))]
        if not bottom or max(bottom)-min(bottom)+1>w*.65:continue
        rows_width=[sum(1 for xx,yy in pts if yy==qy) for qy in range(min(ys),max(ys)+1)]
        if max(rows_width[:max(1,int(h*.08))])>w*.7:continue
        crop=mask[min(ys):max(ys)+1,min(xs):max(xs)+1];background=~crop;outside=np.zeros(crop.shape,dtype=bool)
        todo=[(xx,yy) for yy in range(h) for xx in range(w) if background[yy,xx] and (xx in (0,w-1) or yy in (0,h-1))]
        for xx,yy in todo:outside[yy,xx]=True
        while todo:
            xx,yy=todo.pop()
            for nx,ny in ((xx-1,yy),(xx+1,yy),(xx,yy-1),(xx,yy+1)):
                if 0<=nx<w and 0<=ny<h and background[ny,nx] and not outside[ny,nx]:outside[ny,nx]=True;todo.append((nx,ny))
        if int((background & ~outside).sum())<max(4,int(w*h*.02)):continue
        components.append({'pixel':[statistics.median(bottom),max(ys)],'pixels':len(pts),'bbox':[min(xs),min(ys),max(xs),max(ys)]})
    components.sort(key=lambda v:-v['pixels'])
    if not components:return None,{'reason':'画像に物件ピン形状を検出できない','width':width,'height':height}
    if len(components)>1:return None,{'reason':'同程度のピン候補が複数あり物件ピンを特定できない','candidates':components}
    pin=components[0];world=256*(2**float(zoom));lat,lng=map(float,center)
    x=(lng+180)/360*world;y=(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*world
    x+=(pin['pixel'][0]-width/2)/float(scale);y+=(pin['pixel'][1]-height/2)/float(scale)
    point=(math.degrees(math.atan(math.sinh(math.pi*(1-2*y/world)))),x/world*360-180)
    return point,{'method':'赤色ピンの連結領域・先端認識＋Web Mercator逆投影','center':list(center),'zoom':zoom,'scale':scale,'pin':pin,'width':width,'height':height,'point':list(point)}


def map_image_candidates(web,soup,source_url):
    """Find georeferenced static-map images, then recognize the property pin from pixels."""
    results=[];entries=[]
    for tag in soup.select('img[src],img[data-src],img[data-original]'):
        raw=tag.get('data-src') or tag.get('data-original') or tag.get('src') or ''
        url=urljoin(source_url or '',html.unescape(raw));label=' '.join(str(tag.get(k,'') or '') for k in ('alt','id','class'))
        entries.append((url,label,str(tag.get('data-center','') or ''),tag.get('data-zoom')))
    # SUUMO may keep the Google Static Maps URL in script/data rather than a literal <img> node.
    raw_html=html.unescape(str(soup)).replace('\\/','/').replace('\\u0026','&').replace('\\x26','&')
    for match in re.finditer(r'https://(?:maps\.googleapis\.com|maps\.google\.com)/[^"\'<>\s]*staticmap\?[^"\'<>\s]+',raw_html,re.I):
        url=match.group(0).rstrip('),;');entries.append((url,'script staticmap','',''))
    seen_urls=set()
    for url,label,data_center,data_zoom in entries:
        if not url or url in seen_urls:continue
        seen_urls.add(url);params=parse_qs(urlparse(url).query)
        if not ('staticmap' in url.lower() or re.search('地図|map',label,re.I)):continue
        if re.search('店舗|会社|営業|アクセス',label):continue
        if not ('kankyo' in (source_url or '') or 'FR301FD003' in (source_url or '') or re.search('物件|bukken|property|staticmap',label,re.I)):continue
        center_text=params.get('center',[data_center])[0];match=re.fullmatch(r'\s*(3[4-7]\.\d+)\s*,\s*(1(?:3[89]|40)\.\d+)\s*',center_text)
        try:zoom=float(params.get('zoom',[data_zoom])[0]);scale=float(params.get('scale',[1])[0])
        except (ValueError,TypeError):zoom=None;scale=1
        if not match or zoom is None or not math.isfinite(zoom) or not math.isfinite(scale) or not (0<=zoom<=22 and scale in (1,2)):
            trace(web,'image_georef_missing',{'image_host':urlparse(url).hostname,'reason':'地図画像の基準座標・ズームを読み取れない'},'WARNING','image_location');continue
        supported=urlparse(url).hostname in ('maps.googleapis.com','maps.google.com') and 'staticmap' in urlparse(url).path
        size=re.fullmatch(r'(\d+)x(\d+)',params.get('size',[''])[0])
        if not supported or not size:
            trace(web,'image_georef_missing',{'reason':'地図画像の投影方式・元の画像サイズを検証できない'},'WARNING','image_location');continue
        try:
            if not web.permitted(url):continue
            response=web.fetch(url)
            from PIL import Image
            actual=Image.open(io.BytesIO(response.content)).size
            expected=(int(size[1])*int(scale),int(size[2])*int(scale))
            if actual!=expected:
                trace(web,'image_georef_missing',{'reason':'元画像と宣言サイズが一致せず、拡縮・切り抜きの可能性','actual_size':list(actual),'expected_size':list(expected)},'WARNING','image_location');continue
            point,details=image_pin_point(response.content,(float(match[1]),float(match[2])),zoom,scale)
            details.update(image_url=url,image_sha256=hashlib.sha256(response.content).hexdigest(),projection='Google Static Maps Web Mercator')
            trace(web,'image_pin_result',details,'INFO' if point else 'WARNING','image_location')
            if point:
                if not hasattr(web,'image_point_sources'):web.image_point_sources={}
                web.image_point_sources[point]=url;results.append((*point,'掲載地図画像の物件ピン認識',5))
        except Exception as exc:trace(web,'image_pin_error',{'exception_type':type(exc).__name__,'message':str(exc) if isinstance(exc,AppError) else '画像を解析できない'},'WARNING','image_location')
    return results


def map_links(soup,source_url):
    links=[];origin=urlparse(source_url).hostname
    for tag in soup.select('a[href],iframe[src],[data-url],[data-href],[onclick],form[action]'):
        label=' '.join([tag.get_text(' ',strip=True),str(tag.get('title') or ''),str(tag.get('id') or ''),str(tag.get('class') or ''),str(tag.get('onclick') or ''),str(tag.get('alt') or ''),str(tag.get('aria-label') or '')]+[str(img.get('alt') or '') for img in tag.select('img')])
        targets=[str(tag.get(attr) or '') for attr in ('href','src','data-url','data-href','action')]
        targets.extend(re.findall(r"(?:window\.open|location(?:\.href)?\s*=)\s*\(?\s*['\"]([^'\"]+)['\"]",str(tag.get('onclick') or '')))
        for raw in targets:
            target=urljoin(source_url,html.unescape(raw));u=urlparse(target)
            if not raw or u.scheme!='https' or u.hostname!=origin or u.username or u.password:continue
            if re.search(r'地図|周辺環境|(?:gmap|map|kankyo|FR301FD003)',label+' '+u.path+' '+u.query,re.I) and target!=source_url and target not in links:links.append(target)
    return links


def locate(web,soup,text,address,bounds,munis,hint=None,source_url=None):
    candidates=coordinate(soup,text,hint);source_by_point={p[:2]:source_url for p in candidates}
    if not any(p[3]>=5 for p in candidates):candidates.extend(map_image_candidates(web,soup,source_url))
    # A map-center address lookup must never suppress following the actual property-map link.
    if source_url:
        for target in map_links(soup,source_url):
            try:
                if not web.permitted(target):continue
                reply=web.fetch(target);other=BeautifulSoup(reply.text,'html.parser')
                extra=coordinate(other,reply.text)
                if not any(p[3]>=5 for p in extra):extra.extend(map_image_candidates(web,other,target))
                for p in extra:source_by_point[p[:2]]=target
                candidates.extend(extra)
                trace(web,'map_link_result',{'source_url':source_url,'map_url':target,'candidates':extra},stage='location')
            except AppError as exc:trace(web,'location',{'source_url':source_url,'map_url':target,'message':str(exc)},'WARNING','location')
    candidates=[p for p in candidates if p[3]>=4]
    if any(not in_rectangle(p[:2],bounds) for p in candidates):trace(web,'published_point_outside_bounds',{'points':[list(p[:2]) for p in candidates if not in_rectangle(p[:2],bounds)],'retained':True},stage='location')
    trace(web,'location_candidates',{'address':address,'bounds':bounds,'in_bounds_candidates':candidates},stage='location')
    location_precision='listing_map'
    if candidates:
        lat,lng,method,priority=max(candidates,key=lambda p:p[3])
        distinct={(round(p[0],6),round(p[1],6)) for p in candidates if p[3]==priority}
        if len(distinct)>1:
            trace(web,'map_multiple_candidates',{'method':method,'same_priority_candidates':list(distinct),'retained_as_unresolved':True},'WARNING','location');return None
    elif re.search(r'\d+(?:丁目|番|号)|\d+-\d+',normal(address)):
        data=web.fetch('https://msearch.gsi.go.jp/address-search/AddressSearch',params={'q':address}).json();matches=[]
        for item in data if isinstance(data,list) else []:
            try:
                lng,lat=map(float,item['geometry']['coordinates']);title=normal(item.get('properties',{}).get('title',''))
                if in_rectangle((lat,lng),bounds) and title and address_key(title)==address_key(address):matches.append((lat,lng))
            except (KeyError,ValueError,TypeError):continue
        if not matches:return None
        lat,lng=matches[0];method='住所検索（地図座標を取得できず）';location_precision='address' if re.search(r'\d+[-番]\d+',normal(address)) else 'town'
    else:return None
    try:region=reverse(web,(lat,lng),munis)
    except Exception as exc:
        region=None;trace(web,'reverse_address_pending',{'point':[lat,lng],'exception_type':type(exc).__name__,'retained':True},'WARNING','location')
    inferred=region['label'] if region else '';a,b=address_key(address),address_key(inferred)
    match='逆算住所未確認' if not b else '掲載住所未確認' if not a else '町字一致・番地未検証' if b in a or a==b else '町字不一致・掲載座標を保持'
    # Preserve the published map point and both addresses; inverse geocoding isn't a deletion gate.
    if match=='町字不一致・掲載座標を保持':trace(web,'map_address_difference',{'listing_address':address,'inferred_address':inferred,'point':[lat,lng],'retained':True},'WARNING','location')
    location={'latitude':lat,'longitude':lng,'location_method':method,'map_address':inferred,'inferred_address':inferred,
        'address_match':match,'coordinate_precision':location_precision,'position_source_url':getattr(web,'image_point_sources',{}).get((lat,lng)) or source_by_point.get((lat,lng),source_url or ''),
        'address_precision':'町字（GSI lv01）・番地未確認' if inferred else '未確認'}
    trace(web,'location_ok',{'listing_address':address,'inferred_address':inferred,'match':match,'location':location},stage='location')
    return location


def create_unit(provider,url,title,address,layout,rent,fees,area,floor,age,built_ym,location):
    row=dict(key=hashlib.sha256(url.encode()).hexdigest(),title=str(title or '掲載物件')[:200],address=str(address or '')[:200],
             layout=normal(layout),rent=rent,fees=fees,area=area,floor=str(floor or '')[:80],structure='SRC',age=age,
             built_ym=built_ym,provider=provider,listing_url=url,fetched_at=utc_now(),**location)
    if not valid_unit(row):
        trace(None,'parse',{'reason':'constructed_unit_validation_failed','observed_fields':row},'WARNING','validation');return None
    return row


def homes_area_groups(regions,munis):
    grouped={}
    for region in regions:
        code=str(region.get('code',''));town=normal(region.get('town',''));base=town_base_name(town)
        if not code or not base:continue
        key=(code,address_key(base));row=grouped.get(key)
        municipality=munis.get(code) if isinstance(munis,dict) else None
        city=str(municipality[2]) if isinstance(municipality,(list,tuple)) and len(municipality)>=3 else provider_city_name(region,munis)
        if row is None:
            row=dict(region);row.update(town=base,homes_town=base,city_name=city,visible_towns=[],owner_label=region.get('label',''))
            grouped[key]=row
        if town and town not in row['visible_towns']:row['visible_towns'].append(town)
    return list(grouped.values())


def _named_homes_link(soup,base_url,label,path_hint=''):
    wanted=address_key(label);candidates=[]
    for a in soup.select('a[href]'):
        shown=re.sub(r'[（(][\d,]+(?:件)?[）)]$','',normal(a.get_text(' ',strip=True)))
        if address_key(shown)!=wanted:continue
        target=urljoin(base_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.hostname!='www.homes.co.jp' or not u.path.startswith('/chintai/'):continue
        if path_hint and path_hint not in u.path:continue
        candidates.append(target)
    if not candidates:return None
    return sorted(candidates,key=lambda u:(u.endswith('/list/'),len(u)))[0]


def homes_prepare_region(web,region):
    """Visible-map city/town -> HOME'S city page -> 町域 -> grouped filters."""
    cache=getattr(web,'homes_region_cache',None)
    if cache is None:cache={};web.homes_region_cache=cache
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    if not alias:raise AppError('HOMESの対象都県を確認できません。')
    key=(alias,str(region.get('code','')),address_key(region.get('homes_town') or region.get('town','')))
    if key in cache:return cache[key]
    root=f'https://www.homes.co.jp/chintai/mansion/{alias}/'
    if not web.permitted(root):raise AppError('HOMESの市区郡選択ページの自動取得が許可されていません。')
    root_reply=web.fetch(root);root_soup=BeautifulSoup(root_reply.text,'html.parser')
    city=normal(region.get('city_name') or provider_city_name(region,{}));city_url=_named_homes_link(root_soup,root,city)
    if not city_url:raise AppError('HOMESの市区郡一覧から'+city+'を確認できません。')
    trace(web,'homes_city_selected',{'city':city,'url':city_url,'source':'表示地図から作成した区名・町名リスト'},stage='search_conditions')
    city_reply=web.fetch(city_url);city_soup=BeautifulSoup(city_reply.text,'html.parser')
    wanted=normal(region.get('homes_town') or town_base_name(region.get('town','')))
    town_url=_named_homes_link(city_soup,city_url,wanted,'-town')
    base_params={}
    if town_url:
        town_reply=web.fetch(town_url);base_url=town_url
        trace(web,'homes_town_selected',{'city':city,'town':wanted,'method':'HOMES町域リンク','url':town_url},stage='search_conditions')
    else:
        control=_form_control_by_label(city_soup,wanted)
        if not control:raise AppError('HOMESの「町域の指定」から'+wanted+'を確認できません。')
        name,value=control;base_params[name]=value;base_url=city_url;town_reply=web.fetch(base_url,params=base_params)
        trace(web,'homes_town_selected',{'city':city,'town':wanted,'method':'HOMES町域フォーム','parameter':{name:value}},stage='search_conditions')
    soup=BeautifulSoup(town_reply.text,'html.parser');page_text=normal(soup.get_text(' ',strip=True))
    layout_filters=discover_layout_inputs(soup,'HOME’S')
    for layout in HOMES_LAYOUTS:
        if layout not in layout_filters:
            try:
                params=single_layout_parameters(web,'HOME’S',layout)
                if len(params)==1:layout_filters[layout]=next(iter(params.items()))
            except AppError:pass
    missing=[x for x in HOMES_LAYOUTS if x not in layout_filters]
    if missing:raise AppError('HOMESの間取り選択値を確認できません：'+','.join(missing))
    age15=_form_control_by_label(soup,'15年以内') or _form_control_by_label(city_soup,'15年以内')
    if not age15 and '15年以内' in page_text:age15=('cond[houseageh]','15')
    if not age15:raise AppError('HOMESの「築年数：15年以内」の公開条件を確認できません。')
    mansion=None
    if '/chintai/mansion/' not in urlparse(base_url).path:
        mansion=_form_control_by_label(soup,'マンション') or _form_control_by_label(city_soup,'マンション')
        if not mansion:raise AppError('HOMESの「建物の種類：マンション」を町域ページで確認できません。')
    result={'url':base_url,'base_params':base_params,'layout_filters':layout_filters,'age15':age15,'mansion':mansion,'town':wanted,'city':city,
            'building_type':'マンション'}
    cache[key]=result
    trace(web,'homes_filters_ready',{'city':city,'town':wanted,'building_type':'マンション','age15':age15,'layout_filters':layout_filters},stage='search_conditions')
    return result


def homes_group_params(prepared,layouts):
    params=dict(prepared['base_params']);age_name,age_value=prepared['age15'];params[age_name]=age_value
    if prepared.get('mansion'):
        mansion_name,mansion_value=prepared['mansion'];params[mansion_name]=mansion_value
    grouped={}
    for layout in layouts:
        name,value=prepared['layout_filters'][layout];grouped.setdefault(name,[]).append(value)
    for name,values in grouped.items():params[name]=values if len(values)>1 else values[0]
    return params


def homes_map_url(soup,detail_url):
    for a in soup.select('a[href]'):
        label=' '.join([normal(a.get_text(' ',strip=True)),normal(a.get('title')),normal(a.get('aria-label'))])
        target=urljoin(detail_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.hostname=='www.homes.co.jp' and ('地図を見る' in label or re.search(r'地図|map',label,re.I)) and target!=detail_url:return target
    links=map_links(soup,detail_url)
    return links[0] if links else None


def homes_location_from_map_image(web,detail_soup,detail_url,bounds,munis,expected_towns=None,expected_code=None):
    target=homes_map_url(detail_soup,detail_url)
    if not target:raise AppError('HOMES詳細ページの「地図を見る」を確認できません。')
    if not web.permitted(target):raise AppError('HOMESの地図ページの自動取得が許可されていません。')
    reply=web.fetch(target);soup=BeautifulSoup(reply.text,'html.parser')
    all_candidates=map_image_candidates(web,soup,target)
    candidates=[p for p in all_candidates if p[3]>=5]
    trace(web,'homes_map_image_candidates',{'detail_url':detail_url,'map_url':target,'all_candidates':all_candidates,'usable_candidates':candidates,'bounds':list(bounds)},stage='image_location')
    if not candidates:return None
    priority=max(p[3] for p in candidates);best=[p for p in candidates if p[3]==priority]
    distinct={(round(p[0],6),round(p[1],6)) for p in best}
    if len(distinct)!=1:
        trace(web,'map_multiple_candidates',{'source':'HOMES地図画像','same_priority_candidates':list(distinct)},'WARNING','image_location');return None
    lat,lng,_,_=best[0]
    if not in_rectangle((lat,lng),bounds):
        trace(web,'published_point_outside_bounds',{'map_url':target,'point':[lat,lng],'bounds':list(bounds),'retained':True,'reason':'対象町域の確認を優先し、初期表示範囲外でも保存判定を継続'},stage='location')
    inferred=map_point_to_residential_address(web,(lat,lng),munis,expected_code=expected_code,expected_towns=expected_towns)
    if not inferred:return None
    address=inferred['address']
    if expected_towns and not any(town_matches(t,address) for t in expected_towns):
        trace(web,'map_address_difference',{'source':'HOMES地図画像','map_derived_address':address,'expected_towns':list(expected_towns),'point':[lat,lng],'retained':False},'WARNING','location');return None
    source=getattr(web,'image_point_sources',{}).get((lat,lng),target)
    return {'latitude':lat,'longitude':lng,'location_method':'HOMES「地図を見る」の地図画像ピン認識→GSI住居表示住所推定',
            'map_address':address,'inferred_address':address,'address_match':'地図画像から住所推定',
            'coordinate_precision':'listing_map','position_source_url':source,
            'address_precision':'地図ピン最近傍のGSI住居表示住所（街区符号・基礎番号）'}


def homes_detail(web,url,region,bounds,munis,layouts):
    begin_listing(web,'HOME’S',url)
    text=web.fetch(url).text;soup=BeautifulSoup(text,'html.parser')
    raw_layout=labeled(soup,('間取り','間取'))
    if not raw_layout:
        m=re.search(r'間取り\s*((?:\d+)(?:LDK|DK|K))',normal(soup.get_text(' ',strip=True)),re.I);raw_layout=m.group(1) if m else ''
    layout=parsed_layout(raw_layout)
    if layout not in layouts:return reject_listing(web,'HOME’S',url,'detail.layout','condition' if layout else 'missing_data','間取りが検索条件外、または読み取れない',{'raw':raw_layout,'parsed':layout},list(layouts))
    page_text=normal(soup.get_text(' ',strip=True));kind=labeled(soup,('建物種別','建物の種類','種別'))
    heading=normal(soup.select_one('h1').get_text(' ',strip=True)) if soup.select_one('h1') else ''
    if 'マンション' not in normal(kind+' '+heading+' '+page_text[:600]):return reject_listing(web,'HOME’S',url,'detail.building_type','condition' if kind else 'missing_data','マンションであることを確認できない',{'raw':kind,'heading':heading,'page_excerpt':page_text[:600]},'マンション')
    raw_age=labeled(soup,('築年数','築年月','築年'));age,_=age_info(raw_age)
    if age is not None and age>15:return reject_listing(web,'HOME’S',url,'detail.age','condition','築年数が15年を超える',{'raw':raw_age,'years':age},'15年以内')
    rent=optional_amount(labeled(soup,('賃料','家賃')))
    if not rent:return reject_listing(web,'HOME’S',url,'detail.rent','missing_data','家賃を正の数値として読み取れない',{'raw':labeled(soup,('賃料','家賃')),'parsed':rent},'家賃（円）>0')
    expected_towns=region.get('visible_towns') or [region.get('town','')]
    try:location=homes_location_from_map_image(web,soup,url,bounds,munis,expected_towns,region.get('code'))
    except AppError as exc:
        trace(web,'location_pending',{'url':url,'message':str(exc),'source':'HOMES地図画像'},'WARNING','location');location=None
    if not location or not location.get('inferred_address'):
        trace(web,'location_pending',{'url':url,'reason':'地図画像から住所を確定できないため保存しない','source':'HOMES地図画像'},'WARNING','location');return reject_listing(web,'HOME’S',url,'map.address_inference','location_failure','地図の物件位置から詳細住所を推定できない',location,'番地までの推定住所')
    address=location['inferred_address']
    row=partial_listing('HOME’S',url,'HOMES掲載募集',address,layout,rent,None,None,region,bounds,location,None,age,None,'')

    if not row:return reject_listing(web,'HOME’S',url,'listing.validation','invalid_data','保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'address':address,'location':location},'有効な家賃・間取り・詳細住所・物件URL')
    return row


def homes_collect(web,region,bounds,munis,emit):
    """HOMES: grouped public search, parallel detail verification, map-image address."""
    prepared=homes_prepare_region(web,region);seen_urls=set();visible_towns=region.get('visible_towns') or [region.get('town','')]
    scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
    for group_number,layouts in enumerate(HOMES_LAYOUT_GROUPS,1):
        emit('message',f'HOMES条件{group_number}｜マンション｜築15年以内｜'+ '・'.join(layouts))
        params=homes_group_params(prepared,layouts);current_url=prepared['url'];current_params=params;seen_pages=set();found=0
        while current_url:
            reply=web.fetch(current_url,params=current_params or None);current_params=None;soup=BeautifulSoup(reply.text,'html.parser')
            signature=hashlib.sha256(reply.content).hexdigest()
            if signature in seen_pages:raise AppError('HOMESのページ送りが同じ一覧を返しました。全ページを確認できていません。')
            seen_pages.add(signature);detail_urls=[]
            for a in soup.select('a[href]'):
                label=normal(a.get_text(' ',strip=True));url=urljoin(reply.url,a.get('href','')).split('#')[0]
                if safe_url(url,'HOME’S') and '/chintai/room/' in urlparse(url).path and ('詳細を見る' in label or '物件詳細' in label or label):
                    if url not in seen_urls:seen_urls.add(url);detail_urls.append(url)
            emit('candidate',len(detail_urls));found+=len(detail_urls)

            def detail_work(item):
                while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
                try:
                    index,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
                    emit('message',f'HOMES条件{group_number} 詳細ページ {index}/{len(detail_urls)}件');emit('detail',1)
                    try:
                        begin_listing(web,'HOME’S',url)
                        if not web.permitted(url):raise AppError('HOMES詳細ページの自動取得が許可されていません。')
                        row=homes_detail(web,url,region,bounds,munis,layouts)
                        if row:
                            if visible_towns and row.get('address') and not any(town_matches(t,row['address']) for t in visible_towns):
                                reject_listing(web,'HOME’S',url,'address.town_match','condition','推定住所が対象町域と一致しない',row['address'],list(visible_towns));emit('rejected',1);return 0
                            emit('unit',row);return 1
                        emit('rejected',1);return 0
                    except (AppError,ValueError,TypeError) as exc:
                        trace(web,'listing_failed',{'provider':'HOME’S','url':url,'decision':'failed','failed_stage':'detail.request_or_parse','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__,'observed':getattr(exc,'diagnostic',{}),'expected':'物件詳細と必要項目を取得','evidence':list(getattr(web.local,'listing_evidence',[]))},'ERROR','rejection')
                        emit('issue','HOMES詳細確認｜'+str(exc));trace(web,'detail_optional_error',{'url':url,'message':str(exc)},'WARNING','collector');return 0
                finally:web.detail_slots.release()

            for _,_,error in bounded_results(list(enumerate(detail_urls,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
                if error:
                    trace(web,'detail_optional_error',{'provider':'HOME’S','exception_type':type(error).__name__,'message':'並列詳細処理で予期しない例外'},'WARNING','collector')
                    emit('issue','HOMES詳細確認｜'+type(error).__name__)
            next_url=generic_next_page(soup,reply.url,'HOME’S')
            current_url=next_url
        if not found:trace(web,'empty',{'provider':'HOME’S','region':region.get('label'),'group':list(layouts),'reason':'条件一覧に候補なし'},stage='collector')


def _field_texts(soup,field):
    """Collect visible labels even when SUUMO places checkbox text outside <label>."""
    texts=[]
    if field.get('id'):
        label=soup.find('label',attrs={'for':field['id']})
        if label:texts.append(label.get_text(' ',strip=True))
    label=field.find_parent('label')
    if label:texts.append(label.get_text(' ',strip=True))
    if field.name=='option':texts.append(field.get_text(' ',strip=True))
    texts.extend([field.get('aria-label',''),field.get('data-label',''),field.get('title','')])
    # SUUMO's area modal may render the input and visible town name as siblings.
    parent=field.parent
    for _ in range(4):
        if parent is None:break
        if getattr(parent,'name',None) in ('li','td','th','div','p','span','dd','dt'):
            text=parent.get_text(' ',strip=True)
            if text and len(text)<=160:texts.append(text)
        parent=getattr(parent,'parent',None)
    for sibling in (field.previous_sibling,field.next_sibling):
        if sibling is not None:
            try:text=sibling.get_text(' ',strip=True)
            except AttributeError:text=str(sibling)
            if normal(text):texts.append(text)
    return list(dict.fromkeys(normal(x) for x in texts if normal(x)))

def _form_control_by_label(soup,wanted,names=None):
    """Return the public form name/value whose own label equals the requested visible option."""
    wanted=normal(wanted).replace(' ','')
    for field in soup.select('input[name][value],select[name] option[value]'):
        name=field.get('name') or (field.parent.get('name') if field.parent else '')
        if not name or names and name not in names:continue
        for label in _field_texts(soup,field):
            cleaned=re.sub(r'[（(][\d,]+(?:件)?[）)]$','',label).replace(' ','')
            if cleaned==wanted:
                return name,str(field.get('value',''))
    return None


def suumo_area_groups(regions,munis):
    """Collapse GSI chome points into SUUMO's municipality + town selector level."""
    grouped={}
    for region in regions:
        code=str(region.get('code',''));town=normal(region.get('town',''));base=town_base_name(town)
        if not code or not base:continue
        key=(code,address_key(base));row=grouped.get(key)
        municipality=munis.get(code) if isinstance(munis,dict) else None
        city=str(municipality[2]) if isinstance(municipality,(list,tuple)) and len(municipality)>=3 else provider_city_name(region,munis)
        if row is None:
            row=dict(region)
            row.update(town=base,suumo_town=base,city_name=city,visible_towns=[],owner_label=region.get('label',''))
            grouped[key]=row
        if town and town not in row['visible_towns']:row['visible_towns'].append(town)
    return list(grouped.values())


def _suumo_town_selector(soup,raw_html,wanted,base_url):
    """Resolve SUUMO town selection from links, controls, hidden modal DOM or embedded JSON."""
    wanted=normal(wanted);wanted_key=address_key(wanted)
    for a in soup.select('a[href]'):
        target=urljoin(base_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.hostname!='suumo.jp' or not re.search(r'/chintai/[^/]+/sc_[^/]+/oz_\d+/?$',u.path):continue
        label=re.sub(r'[（(][\d,]+(?:件)?[）)]$','',normal(a.get_text(' ',strip=True)))
        if address_key(label)==wanted_key:return {'url':target,'method':'SUUMO町名URL'}
    # Accept only explicitly labelled town controls. Listing IDs and nearby
    # unrelated town links must never become a town search condition.
    control=_form_control_by_label(soup,wanted,names=('oz',))
    if control and re.fullmatch(r'\d{8}',str(control[1])):
        return {'control':control,'method':'SUUMO町名フォーム'}

    return None


def _suumo_canonical_city_url(soup,response_url,raw_html=''):
    """Resolve the municipality SEO page even when SUUMO hides area controls in script/modal HTML."""
    response_clean=response_url.split('#')[0];u0=urlparse(response_clean)
    if u0.scheme=='https' and u0.hostname=='suumo.jp' and re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u0.path):
        return u0._replace(query='',fragment='').geturl()
    candidates=[]
    canonical=soup.select_one('link[rel="canonical"][href]')
    if canonical:candidates.append(canonical.get('href'))
    og=soup.select_one('meta[property="og:url"][content]')
    if og:candidates.append(og.get('content'))
    for raw in candidates:
        target=urljoin(response_url,raw or '').split('#')[0];u=urlparse(target)
        if u.scheme=='https' and u.hostname=='suumo.jp' and re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u.path):return u._replace(query='',fragment='').geturl()
    for a in soup.select('a[href]'):
        target=urljoin(response_url,a.get('href','')).split('#')[0];u=urlparse(target)
        if u.scheme=='https' and u.hostname=='suumo.jp' and re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u.path):return u._replace(query='',fragment='').geturl()
    decoded=html.unescape(raw_html or '').replace('\\/','/')
    for match in re.finditer(r'(?:https://suumo\.jp)?(/chintai/[^/"\']+/sc_[^/"\']+/)',decoded,re.I):
        target=urljoin('https://suumo.jp',match.group(1));u=urlparse(target)
        if u.hostname=='suumo.jp':return target
    return None


def _suumo_probe_town_url(web,canonical_url,municipality_code,city_name,wanted,max_suffix=120):
    """Fallback: resolve SUUMO's public oz_ town page without using municipality-wide results."""
    if not canonical_url:return None
    u=urlparse(canonical_url)
    if u.hostname!='suumo.jp' or not re.search(r'/chintai/[^/]+/sc_[^/]+/?$',u.path):return None
    code=re.sub(r'\D','',str(municipality_code or ''))
    if not re.fullmatch(r'\d{5}',code):return None
    cache=getattr(web,'suumo_town_probe_cache',None)
    if cache is None:cache={};web.suumo_town_probe_cache=cache
    key=(canonical_url.rstrip('/')+'/',code)
    state=cache.setdefault(key,{'towns':{},'next_suffix':1,'finished':False})
    wanted_key=address_key(wanted)
    if wanted_key in state['towns']:return state['towns'][wanted_key]
    if state.get('finished'):return None
    base=canonical_url.rstrip('/')+'/'
    start=max(1,int(state.get('next_suffix',1)))
    for suffix in range(start,max_suffix+1):
        state['next_suffix']=suffix+1
        target=urljoin(base,f'oz_{code}{suffix:03d}/')
        try:
            if not web.permitted(target):continue
            reply=web.fetch(target);soup=BeautifulSoup(reply.text,'html.parser')
        except AppError as exc:
            trace(web,'suumo_town_probe',{'url':target,'suffix':suffix,'status':'unavailable','message':str(exc)},'WARNING','search_conditions')
            continue
        heading=normal(soup.select_one('h1').get_text(' ',strip=True) if soup.select_one('h1') else '')
        page_title=normal(soup.select_one('title').get_text(' ',strip=True) if soup.select_one('title') else '')
        source=heading or page_title
        town=''
        if city_name:
            match=re.search(re.escape(normal(city_name))+r'(.+?)の賃貸',source)
            if match:town=normal(match.group(1))
        if not town:
            match=re.search(r'^[^ ]+?区(.+?)の賃貸',source)
            if match:town=normal(match.group(1))
        if town:
            state['towns'][address_key(town)]=reply.url.split('#')[0]
            trace(web,'suumo_town_probe',{'url':reply.url,'suffix':suffix,'status':'found','town':town},stage='search_conditions')
            if address_key(town)==wanted_key:return reply.url.split('#')[0]
    state['finished']=True
    return state['towns'].get(wanted_key)


TOKYO_WARDS={str(13101+i):name for i,name in enumerate(('千代田区','中央区','港区','新宿区','文京区','台東区','墨田区','江東区','品川区','目黒区','大田区','世田谷区','渋谷区','中野区','杉並区','豊島区','北区','荒川区','板橋区','練馬区','足立区','葛飾区','江戸川区'))}

def acquisition_cutoff(now=None):
    now=now or datetime.now(timezone.utc);month_index=now.year*12+now.month-1-3
    year,month=divmod(month_index,12);month+=1
    return now.replace(year=year,month=month,day=min(now.day,calendar.monthrange(year,month)[1]))

def suumo_property_id(url):
    u=urlparse(url);bc=parse_qs(u.query).get('bc',[''])[0]
    if re.fullmatch(r'\d+',bc):return 'SUUMO:bc:'+bc
    match=re.search(r'/bc_(\d+)',u.path)
    if match:return 'SUUMO:bc:'+match[1]
    match=re.search(r'/jnc_(\d+)',u.path)
    return 'SUUMO:jnc:'+match[1] if match else None

def recent_acquisition(value,now=None):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None:return False
        now=now or datetime.now(timezone.utc)
        return acquisition_cutoff(now)<=dt<=now
    except (ValueError,TypeError):return False

def suumo_ward_regions(web,code):
    if code not in TOKYO_WARDS:raise AppError('東京23区から対象区を選択してください。')
    url='https://suumo.jp/jj/chintai/ichiran/FR301FC001/'
    params={'ar':'030','bs':'040','ta':'13','sc':code,'pc':'50','srch_navi':'1'}
    if not web.permitted(url):raise AppError('SUUMOの区選択ページの自動取得が許可されていません。')
    reply=web.fetch(url,params=params);soup=BeautifulSoup(reply.text,'html.parser')
    documents=[soup];form=soup.find('form',id='js-machiSelectForm')
    if form and form.get('action'):
        target=urljoin(reply.url,form['action'])
        if urlparse(target).hostname!='suumo.jp' or not web.permitted(target):raise AppError('SUUMOの町名選択ページを確認できません。')
        fields=[(f['name'],f.get('value','')) for f in form.select('input[type="hidden"][name]') if f['name']!='oz']
        popup=web.fetch(target,params=fields);documents.append(BeautifulSoup(popup.text,'html.parser'))
    regions={}
    for document in documents:
        for field in document.select('input[name="oz"][value]'):
            oz=str(field['value'])
            if not re.fullmatch(re.escape(code)+r'\d{3}',oz):continue
            label=document.find('label',attrs={'for':field.get('id')})
            if label is None:label=field.find_parent('label')
            if label is None:continue
            link=label.find('a');name=normal((link or label).get_text(' ',strip=True))
            name=re.sub(r'\s*[（(][\d,]+(?:件)?[）)]\s*$','',name)
            if not name:continue
            prepared={'url':url,'base_params':{**params,'oz':oz},'town':name,'selection_method':'区の公開町名選択フォーム',
                      'mansion':('ts','1'),'age15':('cn','15'),'layout_filters':{layout:('md',value) for layout,value in SUUMO_LAYOUT_CODES.items()}}
            regions[oz]={'code':code,'pref':'13','town':name,'suumo_town':name,'city_name':TOKYO_WARDS[code],
                         'label':'東京都'+TOKYO_WARDS[code]+name,'suumo_prepared':prepared}
    if not regions:raise AppError('選択した区のSUUMO町名リストを取得できません。')
    trace(web,'suumo_ward_towns',{'ward':TOKYO_WARDS[code],'municipality_code':code,'town_count':len(regions)},stage='search_conditions')
    return list(regions.values()),municipalities(web),0


def select_ward_towns(regions,town_codes):
    if not town_codes:return regions
    selected=set(town_codes)
    result=[r for r in regions if r.get('suumo_prepared',{}).get('base_params',{}).get('oz') in selected]
    found={r['suumo_prepared']['base_params']['oz'] for r in result}
    if selected-found:raise AppError('選択した町名をSUUMOの町名一覧で確認できません。町名を読み込み直してください。')
    return result

def ward_town_selector(ward_code,prefix,saved_codes=None,disabled=False):
    cache=st.session_state.setdefault('ward_town_cache',{})
    cached=cache.get(ward_code)
    if st.button('町名を読み込む・更新',key=prefix+'_load_towns',disabled=disabled):
        try:
            with st.spinner('SUUMOの町名一覧を読み込んでいます'):
                web=PublicWeb();web.configure(rental_network_settings())
                regions,munis,_=suumo_ward_regions(web,ward_code)
                cache[ward_code]={'regions':regions,'munis':munis}
                cached=cache[ward_code]
        except Exception as exc:st.error('町名一覧の取得に失敗しました：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
    if not cached:
        st.caption('町名を絞る場合は「町名を読み込む・更新」を押してください。読み込まなくても区全体を検索できます。')
        retained=[c for c in (saved_codes or []) if isinstance(c,str) and re.fullmatch(re.escape(ward_code)+r'\d{3}',c)]
        if retained:st.caption('保存済みの町名指定 '+str(len(retained))+'町を保持しています。変更する場合は町名一覧を読み込んでください。')
        return retained
    options={r['suumo_prepared']['base_params']['oz']:r['town'] for r in cached['regions']}
    widget_key=prefix+'_towns_'+ward_code;stored_key=prefix+'_selected_'+ward_code
    defaults=st.session_state.get(stored_key,saved_codes or [])
    def keep_selection():st.session_state[stored_key]=list(st.session_state.get(widget_key,[]))
    selected=st.multiselect('対象の町名（未選択なら区全体）',list(options),default=[c for c in defaults if c in options],
                          format_func=lambda code:options[code],key=widget_key,disabled=disabled,on_change=keep_selection)
    st.session_state[stored_key]=list(selected)
    return selected


def suumo_prepare_region(web,region):
    """Resolve the visible-map municipality/town to a SUUMO search target.

    Preferred route is SUUMO's public town (oz_) page, which is the result of
    selecting 市区郡 -> 町名. Municipality-wide or free-word address search is
    never substituted when the town selector cannot be confirmed.
    """
    if region.get('suumo_prepared'):return dict(region['suumo_prepared'])
    cache=getattr(web,'suumo_region_cache',None)
    if cache is None:cache={};web.suumo_region_cache=cache
    wanted=normal(region.get('suumo_town') or town_base_name(region.get('town','')))
    key=(str(region.get('pref','')),str(region.get('code','')),address_key(wanted))
    if key in cache:return cache[key]

    search='https://suumo.jp/jj/chintai/ichiran/FR301FC001/'
    city_params={'ar':'030','bs':'040','ta':str(region['pref']),'sc':str(region['code']),'pc':'50','srch_navi':'1'}
    if not web.permitted(search):raise AppError('SUUMOの市区郡選択ページの自動取得が許可されていません。')
    trace(web,'suumo_city_selected',{'municipality_code':region['code'],'city':region.get('city_name',''),'source':'表示地図から作成した市区郡・町名リスト'},stage='search_conditions')

    city_reply=web.fetch(search,params=city_params);city_soup=BeautifulSoup(city_reply.text,'html.parser')
    selector=_suumo_town_selector(city_soup,city_reply.text,wanted,city_reply.url)
    canonical_url=_suumo_canonical_city_url(city_soup,city_reply.url,city_reply.text)
    if not selector:
        form=city_soup.find('form',id='js-machiSelectForm')
        if form and form.get('action'):
            target=urljoin(city_reply.url,form['action'])
            fields=[(f['name'],f.get('value','')) for f in form.select('input[type="hidden"][name]') if f['name'] not in ('oz',)]
            try:
                if urlparse(target).hostname=='suumo.jp' and web.permitted(target):
                    popup=web.fetch(target,params=fields)
                    popup_soup=BeautifulSoup(popup.text,'html.parser')
                    control=_form_control_by_label(popup_soup,wanted,names=('oz',))
                    if control and re.fullmatch(r'\d{8}',str(control[1])):selector={'control':control,'method':'公開町名変更フォーム'}
                    trace(web,'suumo_town_selector_page',{'city':region.get('city_name'),'url':target,'found':bool(control)},stage='search_conditions')
            except AppError as exc:trace(web,'form_alternative',{'failed_url':target,'message':str(exc)},'WARNING','search_conditions')


    if not selector and canonical_url:
        try:
            if web.permitted(canonical_url):
                canonical_reply=web.fetch(canonical_url);canonical_soup=BeautifulSoup(canonical_reply.text,'html.parser')
                selector=_suumo_town_selector(canonical_soup,canonical_reply.text,wanted,canonical_reply.url)
                trace(web,'suumo_town_selector_page',{'city':region.get('city_name',''),'town':wanted,'url':canonical_url,'found':bool(selector)},stage='search_conditions')
        except AppError as exc:
            trace(web,'form_alternative',{'provider':'SUUMO','failed_url':canonical_url,'message':str(exc),'next_public_page':True},'WARNING','search_conditions')

    # The SEO town pages currently use /oz_<municipality><town-seq>/.
    # Probe only after normal selector parsing fails, and cache every town found.
    if not selector and canonical_url:
        probed=_suumo_probe_town_url(web,canonical_url,region.get('code'),region.get('city_name',''),wanted,max_suffix=80)
        if probed:
            selector={'url':probed,'method':'SUUMO町名公開ページ確認'}
            trace(web,'suumo_town_probe_selected',{'city':region.get('city_name',''),'town':wanted,'url':probed},stage='search_conditions')

    if selector and selector.get('url'):
        base_url=urlparse(selector['url'])._replace(query='',fragment='').geturl()
        base_params={'pc':'50'}
        method=selector.get('method','SUUMO町名URL')
    elif selector and selector.get('control'):
        name,value=selector['control'];base_url=search;base_params=dict(city_params);base_params[name]=value
        method=selector.get('method','SUUMO町名フォーム')
    else:
        trace(web,'suumo_town_selector_missing',{'city':region.get('city_name',''),'municipality_code':region.get('code'),'town':wanted},'ERROR','search_conditions')
        raise AppError('SUUMOで「市区郡→町名」の町名ページを確認できません。市区郡全体や住所文字列検索では代用しません。')

    # Current public SUUMO form/query values.  We also verify every accepted
    # detail page, so a provider-side parameter change cannot silently admit a
    # wrong layout/building/age.
    result={
        'url':base_url,
        'base_params':base_params,
        'town':wanted,
        'selection_method':method,
        'mansion':('ts','1'),
        'age15':('cn','15'),
        'layout_filters':{layout:('md',code) for layout,code in SUUMO_LAYOUT_CODES.items()},
    }
    cache[key]=result
    trace(web,'suumo_filters_ready',{'city':region.get('city_name',''),'town':wanted,'selection_method':method,
          'mansion':result['mansion'],'age15':result['age15'],'layout_filters':result['layout_filters']},stage='search_conditions')
    return result


def suumo_group_params(prepared,layouts,page=1):
    params=dict(prepared['base_params'])
    params['ts']='1'                 # 建物の種類：マンション
    params['cn']='15'                # 築年数：15年以内
    params['md']=[SUUMO_LAYOUT_CODES[x] for x in layouts]
    params['pc']='50'
    # SUUMO SEO town pages paginate in the path (pnz1N.html).
    # A form-control town selection can still use FR301, so retain its public page parameter there.
    if '/jj/chintai/ichiran/FR301FC001/' in prepared['url'] and page>1:params['page']=page
    return params


def suumo_detail_fields(soup):
    """Read current SUUMO detail fields without relying on the list address."""
    visible=' '.join(soup.stripped_strings);structured=json_listing_fields(soup)
    # Address text on the detail page is deliberately not read. Location/address acquisition uses the published property map.
    address=''
    raw_layout=structured.get('layout') or generic_labeled(soup,('間取り','間取'))
    if not raw_layout:
        raw_layout=regex_after_label(visible,('間取り','間取'),r'(?:ワンルーム|\d+(?:S?LDK|S?DK|SK|LK|K|L|R))')
    raw_rent=structured.get('rent') or generic_labeled(soup,('賃料','家賃'))
    if not raw_rent:
        # Current SUUMO detail pages show "17.7万円 管理費・共益費: 20000円"
        # above the specification table, so there may be no 賃料 th/dt cell.
        m=re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*万円\s*管理費(?:・共益費)?',visible)
        if m:raw_rent=m.group(1)+'万円'
    if not raw_rent:
        raw_rent=regex_after_label(visible,('賃料','家賃'),r'\d+(?:\.\d+)?\s*万円|\d[\d,]*\s*円')
    building_type=generic_labeled(soup,('建物種別','建物の種類','種別'))
    raw_age=structured.get('age') or generic_labeled(soup,('築年数','築年月','築年'))
    if not raw_age:
        raw_age=regex_after_label(visible,('築年数','築年月','築年'),r'(?:新築|築\s*\d{1,3}年|(?:19|20)\d{2}年\s*\d{1,2}月)')
    return {'visible':visible,'address':normal(address),'raw_layout':normal(raw_layout),'raw_rent':normal(raw_rent),
            'building_type':normal(building_type),'raw_age':normal(raw_age),'structured':structured}


def _suumo_next_url(soup,response_url,filter_params):
    """Follow SUUMO's current path pagination (pnz12.html etc.) and preserve filters."""
    target=generic_next_page(soup,response_url,'SUUMO')
    if not target:return None
    u=urlparse(target)
    if not u.query:
        # Some SEO pagination hrefs omit the current filters in crawled/static HTML.
        # Reattach only the search filters; the oz_ path itself retains the town.
        keep={k:v for k,v in filter_params.items() if k in ('ts','cn','md','pc','ar','bs','ta','sc','srch_navi')}
        target=u._replace(query=urlencode(keep,doseq=True)).geturl()
    return target


def suumo_kankyo_url(soup,detail_url):
    for a in soup.select('a[href]'):
        label=normal(a.get_text(' ',strip=True))+' '+normal(a.get('title'))+' '+normal(a.get('aria-label'))
        target=urljoin(detail_url,a.get('href',''));u=urlparse(target)
        if u.hostname=='suumo.jp' and ('/kankyo/' in u.path or '地図・周辺環境' in label or '周辺環境' in label):return target
    u=urlparse(detail_url);path=u.path.rstrip('/')+'/kankyo/'
    return u._replace(path=path,fragment='').geturl()


def suumo_location_from_kankyo_image(web,detail_soup,detail_url,bounds,munis,expected_towns=None,region_code=None):
    """Read the actual room marker of the published dynamic map, then infer its address."""
    target=suumo_kankyo_url(detail_soup,detail_url)
    if not safe_url(target,'SUUMO'):raise AppError('SUUMOの地図・周辺環境URLを確認できません。')
    if not web.permitted(target):raise AppError('SUUMOの地図・周辺環境ページの自動取得が許可されていません。')
    reply=web.fetch(target);soup=BeautifulSoup(reply.text,'html.parser')
    node=soup.find('script',id='js-gmapData');points=[]
    if node:
        try:
            markers=[];data=json.loads(node.get_text());markers=data.get('markers',[])
            for marker in markers if isinstance(markers,list) else []:
                if not isinstance(marker,dict) or marker.get('type')!='room':continue
                lat,lng=float(marker['lat']),float(marker['lng'])
                if has_point({'latitude':lat,'longitude':lng}):points.append((lat,lng))
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            trace(web,'suumo_marker_invalid',{'map_url':target,'exception_type':type(exc).__name__},'WARNING','location')
    # Never use center coordinates or facility markers as a property position.
    if node:
        distinct=set(points)
        if len(distinct)!=1:
            trace(web,'suumo_marker_unresolved',{'map_url':target,'room_markers':len(distinct),'marker_types':[x.get('type') for x in markers if isinstance(x,dict)] if isinstance(markers,list) else [],'valid_room_points':list(distinct),'expected':'type=roomの有効座標が一意'},'WARNING','location');return None
        lat,lng=next(iter(distinct));method='SUUMO地図・周辺環境の物件マーカー座標→GSI住居表示住所推定'
    else:
        candidates=map_image_candidates(web,soup,target)
        distinct={p[:2] for p in candidates if p[3]>=5}
        if len(distinct)!=1:
            trace(web,'suumo_marker_unresolved',{'map_url':target,'script_found':False,'image_candidates':candidates,'reason':'物件地図スクリプトがなく、画像ピン候補も一意に取得できない'},'WARNING','location');return None
        lat,lng=next(iter(distinct));method='SUUMO地図画像ピン認識→GSI住居表示住所推定'
    if not in_rectangle((lat,lng),bounds):
        trace(web,'published_point_outside_bounds',{'map_url':target,'point':[lat,lng],'bounds':list(bounds),
              'retained':True,'reason':'検索範囲外でも住所推定・保存を継続'},stage='location')
    inferred=map_point_to_residential_address(web,(lat,lng),munis,expected_code=region_code,expected_towns=expected_towns)
    if not inferred:return None
    address=inferred['address']
    if expected_towns and not any(town_matches(t,address) for t in expected_towns):
        trace(web,'map_address_difference',{'map_url':target,'map_derived_address':address,'expected_towns':list(expected_towns),'retained':False},'WARNING','location');return None
    location={'latitude':lat,'longitude':lng,'location_method':method,
              'map_address':address,'inferred_address':address,'address_match':'地図座標から詳細住所推定（実所在地未確認）',
              'coordinate_precision':'listing_map','position_source_url':target,
              'address_precision':'同一町丁目の最近傍住居表示住所・推定値',
              'address_distance_m':round(inferred['distance_m'],2)}
    trace(web,'location_ok',{'source':'SUUMO物件地図マーカー','map_derived_address':address,'location':location},stage='location')
    return location


def suumo_collect(web,region,bounds,munis,emit):
    """SUUMO: map towns -> town page/filter -> detail -> kankyo map -> compact save."""
    prepared=suumo_prepare_region(web,region);seen_urls=set();seen_ids=set()
    # The town search returns every chome. Save the full town, including off-screen properties.
    collection_towns=[town_base_name(region.get('town',''))]
    for group_number,layouts in enumerate(SUUMO_LAYOUT_GROUPS,1):
        filter_params=suumo_group_params(prepared,layouts,1)
        current_url=prepared['url'];current_params=filter_params;seen_pages=set();group_candidates=0;page_number=1
        emit('message',f'SUUMO条件{group_number}｜マンション｜築15年以内｜'+ '・'.join(layouts))
        while current_url:
            reply=web.fetch(current_url,params=current_params if current_params else None);soup=BeautifulSoup(reply.text,'html.parser')
            buildings=soup.select('div.cassetteitem')
            if not buildings:
                page_text=normal(soup.get_text(' ',strip=True))
                if any(t in page_text for t in ('該当する物件','該当物件','物件が見つかりません','0件')):break
                raise AppError(f'SUUMO 条件{group_number}・{page_number}ページ目の一覧を確認できません。')
            signature=hashlib.sha256(str(buildings).encode()).hexdigest()
            if signature in seen_pages:
                raise AppError('SUUMOのページ送りが同じ一覧を返しました。全ページを確認できていません。')
            seen_pages.add(signature);page_candidates=[]

            for building in buildings:
                # Do not read the list-page address. Town scope has already been fixed by the SUUMO town selector.
                rooms=building.select('tr.js-cassette_link')
                if not rooms:
                    rooms=[x for x in building.select('tr') if x.select_one('a[href*="/chintai/"]')]
                for room in rooms:
                    links=[]
                    for a in room.select('a[href]'):
                        label=normal(a.get_text(' ',strip=True));url=urljoin('https://suumo.jp',a.get('href','')).split('#')[0]
                        if safe_url(url,'SUUMO') and ('詳細を見る' in label or re.search(r'/chintai/(?:bc_|jnc_)',urlparse(url).path)):
                            links.append((0 if '詳細を見る' in label else 1,url))
                    if not links:continue
                    url=min(links,key=lambda x:x[0])[1]
                    if url in seen_urls:continue
                    seen_urls.add(url)
                    group_candidates+=1
                    property_id=suumo_property_id(url)
                    if property_id and property_id in seen_ids:continue
                    if property_id:seen_ids.add(property_id)
                    with web.cache_lock:
                        claimed=getattr(web,'claimed_ids',None)
                        if claimed is None:claimed=set();web.claimed_ids=claimed
                        if property_id and property_id in claimed:continue
                        if property_id:claimed.add(property_id)
                    obtained=getattr(web,'acquired_ids',{}).get(property_id)
                    if obtained and recent_acquisition(obtained):
                        begin_listing(web,'SUUMO',url)
                        trace(web,'listing_already_acquired',{'url':url,'property_id':property_id,'last_success_at':obtained,'reason':'過去3か月以内に取得・保存済み'},stage='collector')
                        emit('skipped',1);continue
                    page_candidates.append(url)
            emit('candidate',len(page_candidates))

            scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
            def detail_work(item):
                while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
                try:return detail_work_unbounded(item)
                finally:web.detail_slots.release()
            def detail_work_unbounded(item):
                web.check_cancel()
                index,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
                emit('message',f'条件{group_number} 詳細を見る {index}/{len(page_candidates)}件');emit('detail',1)
                try:
                    begin_listing(web,'SUUMO',url)
                    if not web.permitted(url):raise AppError('SUUMO詳細ページの自動取得が許可されていません。')
                    detail_reply=web.fetch(url);detail=BeautifulSoup(detail_reply.text,'html.parser');fields=suumo_detail_fields(detail)
                    # The detail-page address is intentionally not used for location/address acquisition.
                    # Only rent/layout/building type/age are read here; address comes from the property map below.
                    layout=parsed_layout(fields['raw_layout'],rough=False)
                    if parsed_layout(fields['raw_layout']) not in layouts:
                        reject_listing(web,'SUUMO',url,'detail.layout','condition' if layout else 'missing_data','間取りが検索条件外、または読み取れない',{'raw':fields['raw_layout'],'parsed':layout},list(layouts));emit('rejected',1);trace(web,'layout',{'url':url,'observed':fields['raw_layout'],'parsed':layout,'requested_group':list(layouts),'reason':'detail_layout_not_in_group'},'WARNING');return
                    if 'マンション' not in fields['building_type']:
                        reject_listing(web,'SUUMO',url,'detail.building_type','condition' if fields['building_type'] else 'missing_data','建物種別がマンションと確認できない',fields['building_type'],'マンション');emit('rejected',1);trace(web,'structure',{'url':url,'building_type':fields['building_type'],'reason':'not_mansion'},'WARNING');return
                    age,ym=age_info(fields['raw_age'])
                    if age is None or age>15:
                        reject_listing(web,'SUUMO',url,'detail.age','missing_data' if age is None else 'condition','築年数が15年以内と確認できない',{'raw':fields['raw_age'],'years':age},'15年以内');emit('rejected',1);trace(web,'age',{'url':url,'raw_age':fields['raw_age'],'age':age,'reason':'detail_age_not_confirmed_within_15'},'WARNING');return
                    rent=optional_amount(fields['raw_rent'])
                    if not rent:
                        reject_listing(web,'SUUMO',url,'detail.rent','missing_data','家賃を読み取れない',{'raw':fields['raw_rent'],'parsed':rent},'家賃（円）>0');emit('rejected',1);trace(web,'parse',{'url':url,'missing':'detail_rent','visible_head':fields['visible'][:400]},'WARNING');return

                    try:location=suumo_location_from_kankyo_image(web,detail,url,bounds,munis,collection_towns,region.get('code'))
                    except AppError as exc:
                        location=None;trace(web,'location_pending',{'url':url,'message':str(exc),'source':'SUUMO地図・周辺環境の物件マーカー'},'WARNING','location')
                    address=location.get('inferred_address','') if location else ''
                    if not address:
                        reject_listing(web,'SUUMO',url,'map.address_inference','location_failure','物件位置から詳細住所を推定できない',location,'同一町丁目の90m以内に番地を持つ住所候補');emit('rejected',1);trace(web,'location_pending',{'url':url,'reason':'物件地図から詳細住所を推定できないため保存しない','source':'SUUMO地図・周辺環境の物件マーカー'},'WARNING','location');return

                    # Rent, layout, inferred address and acquisition time survive Database.save_units().
                    row=partial_listing('SUUMO',url,'SUUMO掲載募集',address,layout,rent,None,None,region,bounds,location,None,age,ym,'')
                    if not row:reject_listing(web,'SUUMO',url,'listing.validation','invalid_data','保存必須項目の検証を通らない',{'rent':rent,'layout':layout,'address':address,'location':location},'有効な家賃・間取り・詳細住所・物件URL');emit('rejected',1);return
                    row['property_id']=suumo_property_id(url)
                    row['address_source']=location['location_method']
                    emit('unit',row)
                except (AppError,ValueError,TypeError) as exc:
                    trace(web,'listing_failed',{'provider':'SUUMO','url':url,'decision':'failed','failed_stage':'detail.request_or_parse','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__,'observed':getattr(exc,'diagnostic',{}),'expected':'物件詳細と必要項目を取得','evidence':list(getattr(web.local,'listing_evidence',[]))},'ERROR','rejection');emit('issue','SUUMO詳細確認｜'+str(exc));trace(web,'detail_optional_error',{'url':url,'message':str(exc)},'WARNING','collector')

            for _,_,error in bounded_results(list(enumerate(page_candidates,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
                if error:raise error

            next_url=_suumo_next_url(soup,reply.url,filter_params)
            trace(web,'page_end',{'provider':'SUUMO','group':list(layouts),'page':page_number,'buildings':len(buildings),'new_candidates':len(page_candidates),'next_url':next_url or ''},stage='pagination')
            if not next_url:break
            current_url=next_url;current_params=None;page_number+=1
        if not group_candidates:trace(web,'empty',{'provider':'SUUMO','region':region.get('label'),'group':list(layouts),'reason':'条件一覧に候補なし'},stage='collector')


def provider_city_name(region,munis):
    row=munis.get(str(region.get('code',''))) if isinstance(munis,dict) else None
    if isinstance(row,(list,tuple)) and len(row)>=3:return str(row[2])
    label=normal(region.get('label',''));town=normal(region.get('town',''))
    prefix=label[:-len(town)] if town and label.endswith(town) else label
    return re.sub(r'^(?:東京都|神奈川県|千葉県|埼玉県)','',prefix)


def town_base_name(town):
    value=normal(town).replace('　','').replace(' ','')
    value=re.sub(r'(?:[0-9０-９一二三四五六七八九十]+丁目.*)$','',value)
    return value or normal(town)


def provider_root(provider,region):
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    if not alias:raise AppError('追加取得元は東京都・神奈川県・千葉県・埼玉県で利用できます。')
    roots={
        'スマイティ':f'https://sumaity.com/chintai/{alias}/',
        'カナリー':f'https://web.canary-app.jp/chintai/{alias}/areas/',
        'アットホーム':f'https://www.athome.co.jp/chintai/{alias}/',
        'CHINTAI':f'https://www.chintai.net/{alias}/',
        'Comfy':'https://comfy.maison/',
        'アパマンショップ':f'https://www.apamanshop.com/{alias}/',
    }
    if provider not in roots:raise AppError(provider+'の公開検索入口を確認できません。')
    return roots[provider]


def associated_label_text(soup,field):
    texts=[]
    fid=field.get('id')
    if fid:
        label=soup.find('label',attrs={'for':fid})
        if label:texts.append(label.get_text(' ',strip=True))
    parent=field.find_parent('label')
    if parent:texts.append(parent.get_text(' ',strip=True))
    for node in (field.previous_sibling,field.next_sibling):
        if getattr(node,'get_text',None):texts.append(node.get_text(' ',strip=True))
        elif isinstance(node,str):texts.append(node)
    if field.name=='option':texts.append(field.get_text(' ',strip=True))
    texts.extend([str(field.get('aria-label') or ''),str(field.get('data-label') or ''),str(field.get('title') or '')])
    return ' '.join(x for x in texts if x)


def discover_choice(soup,label,name_hint=None):
    wanted=address_key(label)
    best=None
    for field in soup.select('input[value],option[value]'):
        name=field.get('name') or (field.parent.get('name') if field.parent else '') or ''
        if name_hint and not re.search(name_hint,name,re.I):continue
        value=str(field.get('value','')).strip()
        if not value:continue
        observed=address_key(associated_label_text(soup,field))
        if not observed:continue
        score=3 if observed==wanted else 2 if wanted and wanted in observed else 1 if observed and observed in wanted else 0
        if score and (best is None or score>best[0]):best=(score,name,value)
    if best:return best[1],best[2]
    raw=str(soup)
    escaped=re.escape(normal(label))
    uuid=r'([0-9a-f]{8}-[0-9a-f-]{27,36})'
    patterns=[
        rf'["\'](?:name|label|title)["\']\s*:\s*["\']{escaped}["\'].{{0,300}}?["\'](?:id|value)["\']\s*:\s*["\']{uuid}["\']',
        rf'["\'](?:id|value)["\']\s*:\s*["\']{uuid}["\'].{{0,300}}?["\'](?:name|label|title)["\']\s*:\s*["\']{escaped}["\']',
    ]
    for pat in patterns:
        m=re.search(pat,raw,re.I|re.S)
        if m:return ('cityIds' if '区' in label or '市' in label or '郡' in label else 'areaIds'),m.group(1)
    return None


def find_named_link(soup,base_url,label,host=None,prefer=()):
    wanted=address_key(label);choices=[]
    for a in soup.select('a[href]'):
        href=urljoin(base_url,html.unescape(str(a.get('href') or '')))
        u=urlparse(href)
        if u.scheme!='https' or host and u.hostname!=host:continue
        observed=address_key(a.get_text(' ',strip=True))
        if not observed:continue
        score=0
        if observed==wanted:score=100
        elif wanted and (observed.startswith(wanted) or wanted in observed):score=80
        elif observed and observed in wanted:score=60
        if not score:continue
        score+=sum(5 for p in prefer if re.search(p,u.path+'?'+u.query,re.I))
        if re.search(r'/souba/|/rent/|/sitemap/|/theme/',u.path):score-=20
        choices.append((score,href))
    return max(choices,key=lambda x:x[0])[1] if choices else None


def provider_region_request(web,provider,region,munis,requested):
    provider=canonical_provider(provider);city=provider_city_name(region,munis);town=town_base_name(region.get('town',''))
    alias=PREF_ALIAS.get(str(region.get('pref','')))
    if not alias:raise AppError('対象都県を確認できません。')
    cache_key=('region_request',provider,str(region.get('code')),town,requested if provider in ('カナリー',) else None)
    with web.provider_lock:
        cached=web.provider_cache.get(cache_key)
    if cached:return dict(cached)

    if provider=='CHINTAI':
        # CHINTAI exposes municipality pages and town links; never substitute the city page for a town search.
        city_url=f'https://www.chintai.net/{alias}/area/{region["code"]}/list/'
        if not web.permitted(city_url):raise AppError('CHINTAIの地域ページの自動取得が許可されていません。')
        soup=BeautifulSoup(web.fetch(city_url).text,'html.parser')
        region_url=find_named_link(soup,city_url,town,'www.chintai.net',('area','list'))
        if not region_url:
            raise AppError('CHINTAIで'+town+'の公開町域ページを確認できません。市区町村全体を代用しません。')
        request={'url':region_url,'params':None,'precise':True}

    elif provider=='スマイティ':
        root=provider_root(provider,region);host=PROVIDER_HOST[provider]
        if not web.permitted(root):raise AppError('スマイティの地域検索入口の自動取得が許可されていません。')
        root_soup=BeautifulSoup(web.fetch(root).text,'html.parser')
        city_url=find_named_link(root_soup,root,city,host,('chintai','list','area'))
        if not city_url:raise AppError('スマイティで'+city+'の公開地域ページを確認できません。')
        if not web.permitted(city_url):raise AppError('スマイティの市区町村ページの自動取得が許可されていません。')
        city_soup=BeautifulSoup(web.fetch(city_url).text,'html.parser')
        region_url=find_named_link(city_soup,city_url,town,host,('chintai','zip','area'))
        if not region_url:
            raise AppError('スマイティで'+town+'の公開町域ページを確認できません。市区町村全体を代用しません。')
        request={'url':region_url,'params':None,'precise':True}

    elif provider=='アットホーム':
        # AtHome may reject automated list requests. Discover only public links; do not invent internal endpoints.
        market_index=f'https://www.athome.co.jp/chintai/souba/{alias}/city/'
        if not web.permitted(market_index):raise AppError('アットホームの公開地域入口の自動取得が許可されていません。')
        market_soup=BeautifulSoup(web.fetch(market_index).text,'html.parser')
        market_city=find_named_link(market_soup,market_index,city,'www.athome.co.jp',('souba',))
        if not market_city:raise AppError('アットホームで'+city+'の公開地域ページを確認できません。')
        slug_match=re.search(r'/chintai/souba/'+re.escape(alias)+r'/([^/]+)/?',urlparse(market_city).path)
        if not slug_match:raise AppError('アットホームの市区町村URLを確認できません。')
        list_url=f'https://www.athome.co.jp/chintai/{alias}/{slug_match.group(1)}/list/'
        if not web.permitted(list_url):raise AppError('アットホームの物件一覧の自動取得が許可されていません。')
        list_soup=BeautifulSoup(web.fetch(list_url).text,'html.parser')
        region_url=find_named_link(list_soup,list_url,town,'www.athome.co.jp',('chintai','list','area'))
        if not region_url:
            raise AppError('アットホームで'+town+'の公開町域ページを確認できません。市区町村全体を代用しません。')
        request={'url':region_url,'params':None,'precise':True}

    elif provider=='アパマンショップ':
        # Current public area URLs use the municipality-code suffix (e.g. 13109 -> /tokyo/109/).
        suffix=str(region.get('code',''))[-3:]
        city_url=f'https://www.apamanshop.com/{alias}/{suffix}/'
        if not web.permitted(city_url):raise AppError('アパマンショップの市区町村一覧の自動取得が許可されていません。')
        # No stable public town-only URL is assumed. Candidate cards are filtered by their explicit address before detail fetch.
        request={'url':city_url,'params':None,'precise':False,'filter_context_town':True}

    elif provider=='カナリー':
        root=provider_root(provider,region)
        if not web.permitted(root):raise AppError('カナリーの地域選択ページの自動取得が許可されていません。')
        soup=BeautifulSoup(web.fetch(root).text,'html.parser')
        city_choice=discover_choice(soup,city,r'city')
        if not city_choice:raise AppError('カナリーで'+city+'の地域IDを公開ページから確認できません。')
        city_name,city_value=city_choice
        params={city_name:city_value,'isTotalRent':'false','page':'1','prefectureAlias':alias,'searchType':'1','sortType':'5'}
        if requested in LAYOUTS:params['layouts']=requested
        selected=BeautifulSoup(web.fetch(root,params=params).text,'html.parser')
        town_choice=discover_choice(selected,town,r'(area|town|district)')
        if not town_choice:
            raise AppError('カナリーで'+town+'の町域IDを確認できません。市区町村全体を代用しません。')
        params[town_choice[0]]=town_choice[1]
        request={'url':root,'params':params,'precise':True}

    elif provider=='Comfy':
        # Comfy's public location URLs combine a Kanto group, municipality codes and the town master ID.
        # Build a candidate only from the public Geolonia/ABR town master, then fetch it and verify the page title/text.
        muni=munis.get(str(region.get('code',''))) if isinstance(munis,dict) else None
        if not isinstance(muni,(list,tuple)) or len(muni)<3:
            raise AppError('Comfyの地域URL確認に必要な市区町村情報がありません。')
        pref_name=str(muni[1]);city_name=str(muni[2]);code=str(region.get('code',''))
        town_data=catalog_json(web,'https://japanese-addresses-v2.geoloniamaps.com/api/ja/'+quote(pref_name,safe='')+'/'+quote(city_name,safe='')+'.json')
        exact=[];base=[]
        wanted=address_key(region.get('town',''));wanted_base=address_key(town)
        for row in town_data.get('data',[]):
            if not isinstance(row,dict):continue
            mid=str(row.get('machiaza_id') or '')
            if len(mid)<4 or not mid[:4].isdigit():continue
            label=''.join(str(row.get(k) or '') for k in ('oaza_cho','chome','koaza'))
            key=address_key(label);oaza=address_key(row.get('oaza_cho',''))
            if key==wanted:exact.append(mid[:4])
            if oaza==wanted_base:base.append(mid[:4])
        town_code=next(iter(dict.fromkeys(exact or base)),None)
        if not town_code:
            raise AppError('Comfy用の町域コードを公開住所データから確認できません。')
        # Designated-city wards use their parent city code in the first segment; Tokyo special wards do not.
        parent=code
        if str(region.get('pref')) in ('11','12','14') and '市' in city_name and '区' in city_name and len(code)==5:
            parent=code[:3]+'00'
        region_url=f'https://comfy.maison/location/3-{parent}-{code[-3:]}-{town_code}'
        if not web.permitted(region_url):raise AppError('Comfyの町域ページの自動取得が許可されていません。')
        reply=web.fetch(region_url);page=normal(BeautifulSoup(reply.text,'html.parser').get_text(' ',strip=True))
        if address_key(city_name) not in address_key(page) or wanted_base not in address_key(page):
            raise AppError('Comfyの公開location URLを検証できません。未確認の地域コードは使用しません。')
        request={'url':region_url,'params':None,'precise':True}

    else:raise AppError(provider+'の検索方式がありません。')

    with web.provider_lock:web.provider_cache[cache_key]=dict(request)
    trace(web,'provider_region_discovered',{'provider':provider,'region':region.get('label'),'city':city,'town':town,'request':request},stage='search_conditions')
    return request

def generic_next_page(soup,current_url,provider):
    host=PROVIDER_HOST.get(provider);candidates=[]
    for a in soup.select('a[href]'):
        label=normal(a.get_text(' ',strip=True)).upper()
        if not (re.search(r'次(?:の)?(?:\d+件|ページ|へ)|NEXT',label,re.I) or label in ('›','»','>','≫')):continue
        target=urljoin(current_url,html.unescape(str(a.get('href') or '')));u=urlparse(target)
        if u.scheme=='https' and u.hostname==host and target!=current_url:candidates.append(target)
    return candidates[0] if candidates else None


def _candidate_context(anchor,max_levels=7):
    parts=[];node=anchor
    for _ in range(max_levels):
        node=getattr(node,'parent',None)
        if node is None:break
        if getattr(node,'name',None) in ('body','html'):break
        try:text=node.get_text(' ',strip=True)
        except Exception:text=''
        if text:parts.append(text)
        if getattr(node,'name',None) in ('article','li','tr'):break
    return normal(' '.join(parts))


def detail_link_candidates(soup,base_url,provider,town=None,require_town_context=False):
    provider=canonical_provider(provider);host=PROVIDER_HOST[provider];out=[];seen=set()
    strong={
        'スマイティ':r'^/chintai/[^/]*_prop/prop_\d+/?$',
        'カナリー':r'^/chintai/(?:rooms?|buildings?|properties?)/[0-9a-f-]+/?$',
        'アットホーム':r'^/chintai/\d+/?$',
        'CHINTAI':r'^/detail/bk-[A-Za-z0-9_-]+/?$',
        'Comfy':r'^/(?:properties|rooms|rentals)/[^/]+/?$',
        'アパマンショップ':r'^/(?:tokyo|kanagawa|saitama|chiba)/\d{2,3}/b\d+/\d+/?$',
    }.get(provider,r'')
    wanted=address_key(town or '')
    for a in soup.select('a[href]'):
        url=urljoin(base_url,html.unescape(str(a.get('href') or ''))).split('#')[0];u=urlparse(url)
        if url in seen or u.scheme!='https' or u.hostname!=host or not safe_url(url,provider):continue
        label=normal(a.get_text(' ',strip=True));context=_candidate_context(a);path=u.path
        if re.search(r'/souba/|/sitemap/|/theme/|/rent/|/areas?/?$|/stations?/',path,re.I):continue
        is_strong=bool(strong and re.search(strong,path,re.I))
        contextual=bool(re.search(r'詳細|物件|部屋|賃貸',label)) and bool(re.search(r'\d+(?:\.\d+)?\s*万円|\d{4,}\s*円',context))
        if not (is_strong or contextual):continue
        if require_town_context and wanted:
            if not (town_matches(town,context) or wanted in address_key(context)):
                continue
        seen.add(url);out.append(url)
    return out

def generic_labeled(soup,labels):
    value=labeled(soup,tuple(labels))
    if value:return value
    wanted={normal(x).replace(' ','') for x in labels}
    for row in soup.select('tr'):
        cells=row.find_all(['th','td'],recursive=False)
        if len(cells)>=2 and normal(cells[0].get_text(' ',strip=True)).replace(' ','') in wanted:
            return cells[1].get_text(' ',strip=True)
    for dt in soup.select('dt'):
        if normal(dt.get_text(' ',strip=True)).replace(' ','') in wanted:
            dd=dt.find_next_sibling('dd')
            if dd:return dd.get_text(' ',strip=True)
    return ''


def json_listing_fields(soup):
    out={}
    for script in soup.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.get_text())
        except Exception:continue
        for node in json_nodes(data):
            if not isinstance(node,dict):continue
            if not out.get('title') and node.get('name') and node.get('@type') not in ('Organization','RealEstateAgent','LocalBusiness'):
                out['title']=str(node.get('name'))
            address=node.get('address')
            if isinstance(address,dict) and not out.get('address'):
                out['address']=''.join(str(address.get(k,'') or '') for k in ('addressRegion','addressLocality','streetAddress'))
            offer=node.get('offers')
            if isinstance(offer,dict) and not out.get('rent'):out['rent']=offer.get('price')
            size=node.get('floorSize')
            if isinstance(size,dict) and not out.get('area'):out['area']=size.get('value')
            props=node.get('additionalProperty')
            if isinstance(props,list):
                for item in props:
                    if not isinstance(item,dict):continue
                    name=normal(item.get('name'));value=item.get('value')
                    if name in ('間取り','間取'):out.setdefault('layout',value)
                    elif name in ('建物構造','構造'):out.setdefault('structure',value)
                    elif name in ('築年月','築年数'):out.setdefault('age',value)
                    elif name in ('管理費等','管理費','共益費'):out.setdefault('fees',value)
    return out


def regex_after_label(text,labels,value_pattern):
    for label in labels:
        m=re.search(re.escape(label)+r'\s*[:：]?\s*('+value_pattern+r')',text,re.I)
        if m:return m.group(1).strip()
    return ''


def generic_detail(web,provider,url,region,bounds,munis,requested):
    begin_listing(web,provider,url)
    if not web.permitted(url):raise AppError(provider+'の詳細ページの自動取得が許可されていません。')
    reply=web.fetch(url);soup=BeautifulSoup(reply.text,'html.parser');visible=' '.join(soup.stripped_strings);structured=json_listing_fields(soup)
    raw_layout=structured.get('layout') or generic_labeled(soup,('間取り','間取')) or regex_after_label(visible,('間取り','間取'),r'(?:ワンルーム|\d+(?:S?LDK|S?DK|SK|LK|K|L|R))')
    layout=resolve_layout(web,raw_layout,requested,url)
    if layout is None:return reject_listing(web,provider,url,'detail.layout','condition' if parsed_layout(raw_layout) else 'missing_data','間取りが検索条件外、または読み取れない',raw_layout,requested or list(LAYOUTS))
    address=structured.get('address') or generic_labeled(soup,('所在地','住所','物件所在地','所在地住所'))
    if not address:
        m=re.search(r'(東京都|神奈川県|千葉県|埼玉県)[^|｜\n]{2,80}?(?=(?:交通|最寄|駅徒歩|間取り|賃料|家賃|築|$))',visible)
        if m:address=m.group(0).strip()
    if address and not town_matches(region['town'],address):
        trace(web,'address',{'provider':provider,'town':region['town'],'listing_address':address,'url':url},'WARNING','parser');return reject_listing(web,provider,url,'address.town_match','condition','掲載住所が対象町域と一致しない',address,region['town'])
    title=structured.get('title') or (soup.select_one('h1').get_text(' ',strip=True) if soup.select_one('h1') else provider+'掲載募集')
    raw_rent=structured.get('rent') or generic_labeled(soup,('賃料','家賃'))
    if not raw_rent:raw_rent=regex_after_label(visible,('賃料','家賃'),r'\d+(?:\.\d+)?\s*万円|\d[\d,]*\s*円')
    rent=optional_amount(raw_rent)
    raw_fees=structured.get('fees') or generic_labeled(soup,('管理費等','管理費・共益費','管理費','共益費'))
    if not raw_fees:raw_fees=regex_after_label(visible,('管理費等','管理費・共益費','管理費','共益費'),r'(?:なし|無料|－|-|\d+(?:\.\d+)?\s*万円|\d[\d,]*\s*円)')
    fees=optional_amount(raw_fees)
    raw_area=structured.get('area') or generic_labeled(soup,('専有面積','面積'))
    if not raw_area:raw_area=regex_after_label(visible,('専有面積','面積'),r'\d+(?:\.\d+)?\s*(?:m2|m²|㎡|平米)')
    area_match=re.search(r'\d+(?:\.\d+)?',normal(raw_area));area=float(area_match.group(0)) if area_match else None
    structure=structured.get('structure') or generic_labeled(soup,('建物構造','構造','種別/構造'))
    raw_age=structured.get('age') or generic_labeled(soup,('築年月','築年数','築年'))
    if not raw_age:
        raw_age=regex_after_label(visible,('築年月','築年数','築年'),r'(?:新築|築\s*\d{1,3}年|(?:19|20)\d{2}年\s*\d{1,2}月)')
    age,ym=age_info(raw_age)
    floor=generic_labeled(soup,('所在階','階建 / 階','階建/階','階数','階'))
    try:location=locate(web,soup,reply.text,address,bounds,munis,source_url=url)
    except AppError as exc:location=None;trace(web,'location',{'provider':provider,'url':url,'message':str(exc)},'WARNING','location')
    row=partial_listing(provider,url,title,address or region['label'],layout,rent,fees,area,region,bounds,location,structure or None,age,ym,floor)
    if row:trace(web,'detail_fields',{'provider':provider,'url':url,'layout':layout,'rent':rent,'fees':fees,'area':area,'address':address,'missing_fields':row.get('missing_fields',[])},stage='parser')
    if not row:return reject_listing(web,provider,url,'listing.validation','invalid_data','保存必須項目の検証を通らない',{'rent':rent,'raw_rent':raw_rent,'layout':layout,'address':address,'location':location},'有効な家賃・間取り・住所・物件URL')
    return row


def public_portal_collect(web,region,bounds,munis,emit,provider):
    provider=canonical_provider(provider);requested=region.get('search_layout')
    if requested is not None and requested not in ALL_TARGET_LAYOUTS:raise AppError(provider+'の間取り指定を確認してください。')
    request=provider_region_request(web,provider,region,munis,requested)
    cache_key=('listing_links',provider,str(region.get('code')),address_key(region.get('town','')),requested if provider=='カナリー' and requested in ALL_TARGET_LAYOUTS else 'all')
    with web.provider_lock:cached=web.provider_cache.get(cache_key)
    links=list(cached) if isinstance(cached,list) else []
    if not links:
        current=request['url'];params=dict(request.get('params') or {});seen_pages=set();links_seen=set();page=1
        while current:
            emit('message',f'{provider} 公開一覧 {page}ページ目')
            if not web.permitted(current):raise AppError(provider+'の一覧ページの自動取得が許可されていません。')
            reply=web.fetch(current,params=params or None);params={}
            soup=BeautifulSoup(reply.text,'html.parser');signature=hashlib.sha256(reply.content).hexdigest()
            if signature in seen_pages:raise AppError(provider+'のページ送りが同じ一覧を返しました。全ページを確認できていません。')
            seen_pages.add(signature)
            found=[u for u in detail_link_candidates(soup,reply.url,provider,region.get('town'),bool(request.get('filter_context_town'))) if u not in links_seen]
            for u in found:links_seen.add(u);links.append(u)
            emit('candidate',len(found))
            next_url=generic_next_page(soup,reply.url,provider)
            trace(web,'page_end',{'provider':provider,'region':region['label'],'page':page,'new_candidates':len(found),'next_url':next_url},stage='pagination')
            if not next_url:break
            current=next_url;page+=1
        with web.provider_lock:web.provider_cache[cache_key]=list(links)
    if not links:
        raise AppError(provider+'で'+region['label']+'の募集詳細URLを公開一覧から確認できません。')

    scope=dict(getattr(DIAG_CONTEXT,'scope',{}))
    def detail_work(item):
        while not web.detail_slots.acquire(timeout=.1):web.check_cancel()
        try:
            i,url=item;DIAG_CONTEXT.audit=getattr(web,'audit',None);DIAG_CONTEXT.scope=dict(scope)
            emit('message',f'{provider} 掲載項目の確認 {i}/{len(links)}件');emit('detail',1)
            try:row=generic_detail(web,provider,url,region,bounds,munis,requested)
            except Exception as exc:
                trace(web,'detail_optional_error',{'provider':provider,'url':url,'exception_type':type(exc).__name__,'message':str(exc) if isinstance(exc,AppError) else '詳細追加確認失敗'},'WARNING','collector')
                trace(web,'listing_failed',{'provider':provider,'url':url,'decision':'failed','failed_stage':'detail.request_or_parse','category':'acquisition_failure','reason':str(exc),'exception_type':type(exc).__name__,'observed':getattr(exc,'diagnostic',{}),'expected':'物件詳細と必要項目を取得','evidence':list(getattr(web.local,'listing_evidence',[]))},'ERROR','rejection')
                emit('issue',provider+'｜'+(str(exc) if isinstance(exc,AppError) else '詳細ページを確認できません。'));return 0
            if row:emit('unit',row);return 1
            emit('rejected',1);return 0
        finally:web.detail_slots.release()

    accepted=0
    for _,value,error in bounded_results(list(enumerate(links,1)),detail_work,workers=DETAIL_PAGE_WORKERS,stop_event=web.cancel_event):
        if error:
            trace(web,'detail_optional_error',{'provider':provider,'exception_type':type(error).__name__,'message':'並列詳細処理で予期しない例外'},'WARNING','collector')
            emit('issue',provider+'｜'+type(error).__name__)
        elif value:accepted+=int(value)
    trace(web,'provider_collect_finish',{'provider':provider,'region':region['label'],'layout':requested,'detail_links':len(links),'accepted':accepted,'detail_workers':DETAIL_PAGE_WORKERS},stage='collector')


def sumaity_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'スマイティ')


def canary_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'カナリー')


def athome_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'アットホーム')


def chintai_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'CHINTAI')


def comfy_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'Comfy')


def apaman_collect(web,region,bounds,munis,emit):
    return public_portal_collect(web,region,bounds,munis,emit,'アパマンショップ')


PROVIDER_COLLECTORS={
    'HOME’S':homes_collect,'SUUMO':suumo_collect,'スマイティ':sumaity_collect,'カナリー':canary_collect,
    'アットホーム':athome_collect,'CHINTAI':chintai_collect,'Comfy':comfy_collect,'アパマンショップ':apaman_collect,
}



def search_all(db,conditions,screen,state=None):
    if state is None: state=st.session_state
    bounds=tuple(conditions['bounds']);started=time.monotonic()
    audit=getattr(state,'audit',None) or AuditLog(conditions,(getattr(db,'key',''),));state.audit=audit;db.audit=audit
    DIAG_CONTEXT.audit=audit;DIAG_CONTEXT.scope={}
    search=dict(id=audit.search_id,status='started',
                started_at=utc_now(),finished_at=None,conditions=conditions,summary={})
    units={};saved=set();issues=[];logs=[];acquisition_pending={};q=None
    def log(message):
        audit.add('progress',diagnosis_code(message) if 'エラー' in message or '失敗' in message else 'progress','INFO',{'message':message})
        logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        screen['log'].code('\n'.join(logs[-15:]),language=None)
    screen['status'].info('検索実行中｜保存先の接続・新しいテーブルを確認しています')
    if not getattr(db,'collection_checked',False):db.check();db.collection_checked=True
    db.save_search(search);log('保存先確認OK。新しい検索を開始しました。')
    web=getattr(state,'shared_web',None) or PublicWeb();web.audit=audit
    web.address_points=getattr(db,'address_points',None)
    web.http_cache.clear();web.failed_detail_urls.clear();web.unavailable_hosts.clear();web.claimed_ids=set()
    if hasattr(state,'request_starts'):web.host_last_request=state.request_starts
    if hasattr(state,'cancel_event'):web.cancel_event=state.cancel_event
    if hasattr(web,'configure'):web.configure(getattr(db,'web_config',{}))
    def region_update(stage,done,total,count,errors):
        if done and done==total:audit.add('geography','region_progress','INFO',{'stage':stage,'done':done,'total':total,'regions':count,'failures':errors})
        audit.persist(db)
        if stage.startswith('地名取得先を切替'): log(stage)
        screen['bar'].progress(.05+.15*done/max(1,total),text=f'{stage} {done}/{total}')
        screen['status'].info(f'検索実行中｜{stage} {done}/{total}｜{count}地域｜取得失敗 {errors}件｜経過 {int(time.monotonic()-started)}秒')
    try:
        if 'SUUMO' in conditions['providers']:web.acquired_ids=db.load_recent_acquisition_ids()
        screen['status'].info('検索実行中｜地域の町名リストを取得しています')
        if conditions.get('auto_regions') and conditions.get('auto_munis'):
            regions,munis,region_errors=conditions['auto_regions'],conditions['auto_munis'],0
        elif conditions.get('ward_code'):
            regions,munis,region_errors=suumo_ward_regions(web,conditions['ward_code'])
        else:
            regions,munis,region_errors=regional_tasks(web,bounds,region_update)
        if conditions.get('ward_code'):
            regions=select_ward_towns(regions,conditions.get('town_codes',[]))
        if conditions.get('mode')=='automatic_collection':
            conditions['auto_regions']=regions;conditions['auto_munis']=munis
        if region_errors: issues.append(f'地域判定で{region_errors}地点を確認できませんでした。')
        search['conditions']['region_source']='SUUMO区の公開町名選択フォーム' if conditions.get('ward_code') else 'Geolonia町丁目一覧' if getattr(web,'reverse_unavailable',False) else '国土地理院地名判定'
        log('地名取得完了｜'+search['conditions']['region_source']+'｜'+str(len(regions))+'地域')
        search['conditions']['regions']=[r['label'] for r in regions]
        state.new_regions=[r['label'] for r in regions]
        conditions['providers']=list(dict.fromkeys(canonical_provider(p) for p in conditions['providers']))
        suumo_groups={};homes_groups={}
        if 'SUUMO' in conditions['providers']:
            for group in suumo_area_groups(regions,munis):suumo_groups[(str(group['code']),address_key(group['suumo_town']))]=group
            search['conditions']['suumo_areas']=[{'municipality_code':g['code'],'city':g.get('city_name',''),'town':g['suumo_town'],'visible_towns':list(g.get('visible_towns',[]))} for g in suumo_groups.values()]
            audit.add('search_conditions','suumo_area_list','INFO',{'areas':search['conditions']['suumo_areas']})
        if 'HOME’S' in conditions['providers']:
            for group in homes_area_groups(regions,munis):homes_groups[(str(group['code']),address_key(group['homes_town']))]=group
            search['conditions']['homes_areas']=[{'municipality_code':g['code'],'city':g.get('city_name',''),'town':g['homes_town'],'visible_towns':list(g.get('visible_towns',[]))} for g in homes_groups.values()]
            audit.add('search_conditions','homes_area_list','INFO',{'areas':search['conditions']['homes_areas']})
        available={}
        generic_providers=[]
        for provider in conditions['providers']:
            if provider not in PROVIDER_COLLECTORS:
                issues.append(provider+'｜未対応の取得元です。');audit.add('search_conditions','provider_unavailable','ERROR',{'provider':provider,'message':'collector not registered'});continue
            if provider=='SUUMO':
                available[provider]=['GROUPED']
                audit.add('search_conditions','provider_enabled','INFO',{'provider':'SUUMO','building_type':'マンション','max_age':15,'layout_groups':[list(x) for x in SUUMO_LAYOUT_GROUPS],'filter_mode':'city -> town -> grouped layouts -> detail -> kankyo map image'})
                continue
            if provider=='HOME’S':
                available[provider]=['GROUPED']
                audit.add('search_conditions','provider_enabled','INFO',{'provider':'HOME’S','building_type':'マンション','max_age':15,'layout_groups':[list(x) for x in HOMES_LAYOUT_GROUPS],'filter_mode':'city -> 町域 -> grouped layouts -> detail -> 地図を見る image'})
                continue
            available[provider]=['ALL'];generic_providers.append(provider)
            audit.add('search_conditions','provider_enabled','INFO',{'provider':provider,'layouts':list(ALL_TARGET_LAYOUTS),'filter_mode':'one public town-list crawl + explicit detail layout verification'})
        jobs=[];provider_targets=[]
        for provider in conditions['providers']:
            if provider not in available:continue
            targets=list(suumo_groups.values()) if provider=='SUUMO' else list(homes_groups.values()) if provider=='HOME’S' else regions
            provider_targets.append((provider,targets))
        for number in range(max((len(targets) for _,targets in provider_targets),default=0)):
            for provider,targets in provider_targets:
                if number<len(targets):jobs.append((targets[number],provider))
        if conditions.get('mode')=='automatic_collection':
            if not jobs:raise AppError('自動収集する町が見つかりません。対象範囲を変更してください。')
            index=int(conditions.get('auto_task_index',0))%len(jobs)
            conditions['auto_total_tasks']=len(jobs);conditions['auto_task_index']=index
            conditions['auto_task_label']=jobs[index][0]['label']+'｜'+jobs[index][1]
            jobs=[jobs[index]]
        db.job_new_keys=set()
        q=queue.Queue();active={};done=0;candidate=detail=rejected=skipped=incomplete=0;save_buffer={};last_flush=time.monotonic()
        def work(index,region,provider):
            if web.cancel_event.is_set():return
            DIAG_CONTEXT.audit=audit;DIAG_CONTEXT.scope={'task':index,'region':region['label'],'town':region['town'],'municipality_code':region['code'],'provider':provider}
            def emit(kind,value):
                if kind!='unit':web.check_cancel()
                audit_value=compact_saved_listing(value) if kind=='unit' and isinstance(value,dict) else value
                if kind=='unit' and isinstance(audit_value,dict) and value.get('property_id'):audit_value={**audit_value,'property_id':value['property_id']}
                audit.add('collector','accepted' if kind=='unit' else kind,'ERROR' if kind=='issue' else 'INFO',{'event':kind,'value':audit_value})
                q.put((index,kind,value))
            try:
                emit('message',provider+'｜'+region['label']+'｜検索を開始')
                PROVIDER_COLLECTORS[provider](web,dict(region,search_layout=None),bounds,munis,emit)
            except SearchCancelled:return
            except Exception as exc:
                trace(web,diagnosis_code(str(exc)),{'exception_type':type(exc).__name__,'traceback':traceback.format_exc()},'ERROR','collector_exception')
                emit('issue',provider+'｜'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
                emit('incomplete',1)
        with futures.ThreadPoolExecutor(max_workers=max(1,min(SEARCH_TASK_WORKERS,len(jobs)))) as pool:
            pending={};next_job=0
            def fill_jobs():
                nonlocal next_job
                while next_job<len(jobs) and len(pending)<SEARCH_TASK_WORKERS and not web.cancel_event.is_set():
                    pending[pool.submit(work,next_job,*jobs[next_job])]=next_job;next_job+=1
            fill_jobs()
            completed=set()
            while pending or not q.empty() or save_buffer:
                ready,_=futures.wait(pending,timeout=.35,return_when=futures.FIRST_COMPLETED) if pending else (set(),set())
                if web.cancel_event.is_set():
                    for f in pending:f.cancel()
                audit.persist(db)
                batch=[]
                while True:
                    try: index,kind,value=q.get_nowait()
                    except queue.Empty: break
                    label=jobs[index][0]['label']+'｜'+jobs[index][1]
                    if kind=='message' and index not in completed: active[index]=label+'｜'+str(value)
                    elif kind=='candidate': candidate+=int(value)
                    elif kind=='detail': detail+=int(value)
                    elif kind=='rejected': rejected+=int(value)
                    elif kind=='skipped': skipped+=int(value)
                    elif kind=='incomplete': incomplete+=int(value)
                    elif kind=='issue':
                        issues.append(label+'｜'+str(value));log(issues[-1])
                    elif kind=='unit':
                        identity=compact_listing_key(value) or value['key'];value=dict(value,key=identity)
                        units[identity]=merge_listing(units.get(identity),value)
                        batch.append(units[identity])
                for row in batch:
                    key=compact_listing_key(row);save_buffer[key]=newest_observation(save_buffer.get(key),row)
                batch=list(save_buffer.values()) if save_buffer and (len(save_buffer)>=100 or time.monotonic()-last_flush>=3 or web.cancel_event.is_set() or (len(ready)==len(pending) and next_job==len(jobs))) else []
                if batch:
                    save_buffer.clear();last_flush=time.monotonic()
                    state.new_units=list(units.values())
                    screen['status'].info(f'検索実行中｜確認した{len(batch)}件をSupabaseへ保存しています')
                    try:
                        for offset in range(0,len(batch),200):
                            chunk=batch[offset:offset+200];saved.update(db.save_units(chunk));state.new_last_saved_at=utc_now()
                            for row in chunk:
                                if row.get('property_id'):acquisition_pending[row['property_id']]=row
                            db.save_acquisition_ids(chunk)
                            for row in chunk:acquisition_pending.pop(row.get('property_id'),None)
                            for row in chunk:
                                if row.get('property_id'):web.acquired_ids[row['property_id']]=row['fetched_at']
                    except Exception as exc: issues.append(str(exc) if isinstance(exc,AppError) else type(exc).__name__);log('保存エラー｜'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
                    # Browser session holds unconfirmed rows separately; no claim of persistence.
                    state.new_units=list(units.values())
                    state.new_saved_keys=list(saved)
                for f in ready:
                    index=pending.pop(f);completed.add(index);active.pop(index,None);done+=1
                    if not f.cancelled():
                        try:f.result()
                        except SearchCancelled:pass
                    log(f'{done}/{len(jobs)}完了｜'+jobs[index][0]['label']+'｜'+jobs[index][1])
                fill_jobs()
                screen['bar'].progress(.2+.75*done/max(1,len(jobs)),text=f'物件確認 {done}/{len(jobs)}タスク｜保存確認 {len(saved)}件')
                screen['status'].info(f'検索実行中｜{done}/{len(jobs)}タスク完了｜候補 {candidate}｜詳細 {detail}｜除外 {rejected}｜取得済みスキップ {skipped}｜保存確認 {len(saved)}件｜経過 {int(time.monotonic()-started)}秒\n\n'+'\n\n'.join(active.values()))
        search['status']='cancelled' if web.cancel_event.is_set() else 'partial' if issues else 'completed'
        search['summary']={'confirmed':len(units),'saved':len(saved),'candidate':candidate,'detail':detail,'rejected':rejected,'already_acquired':skipped,'incomplete_tasks':incomplete,
                           'retained_with_missing_fields':sum(bool(r.get('missing_fields')) for r in units.values()),'price_unknown':sum(not r.get('rent') for r in units.values()),'issues':issues[-100:],'elapsed':round(time.monotonic()-started,3),'tasks':len(jobs)}
    except SearchCancelled:
        search['status']='cancelled'
        search['summary']={'confirmed':len(units),'saved':len(saved),'issues':issues,'elapsed':round(time.monotonic()-started,3)}
    except Exception as exc:
        search['status']='failed';issues.append(str(exc) if isinstance(exc,AppError) else f'検索エラー（{type(exc).__name__}）')
        search['summary']={'confirmed':len(units),'saved':len(saved),'issues':issues,'elapsed':round(time.monotonic()-started,3)}
    # On a consumer exception the executor has joined its workers. Preserve all
    # accepted rows still in the queue before final persistence, including stop.
    if q is not None:
        while True:
            try:_,kind,value=q.get_nowait()
            except queue.Empty:break
            if kind=='unit':
                identity=compact_listing_key(value) or value['key'];value=dict(value,key=identity)
                units[identity]=merge_listing(units.get(identity),value)
    # Workers have finished and the event queue is drained before this final save.
    # Cancellation only stops acquisition; database writes remain enabled.
    remaining=[r for r in units.values() if compact_listing_key(r) not in saved]
    if remaining or acquisition_pending:
        screen['status'].info('最終保存中｜取得済みデータの保存を確認しています')
    for offset in range(0,len(remaining),200):
        chunk=remaining[offset:offset+200]
        try:
            saved.update(db.save_units(chunk));state.new_last_saved_at=utc_now()
            for row in chunk:
                if row.get('property_id'):acquisition_pending[row['property_id']]=row
        except Exception as exc:
            audit.add('database','stop_save_failed','ERROR',{'stage':'stop.final_save' if web.cancel_event.is_set() else 'search.final_save','exception_type':type(exc).__name__,'rows':len(chunk)})
            issues.append('最終保存での物件保存失敗：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
    if acquisition_pending:
        try:
            db.save_acquisition_ids(list(acquisition_pending.values()));acquisition_pending.clear()
        except Exception as exc:issues.append('最終保存での取得済みID保存失敗：'+(str(exc) if isinstance(exc,AppError) else type(exc).__name__))
    unsaved=sum(compact_listing_key(r) not in saved for r in units.values())
    search['summary'].update(confirmed=len(units),elapsed=round(time.monotonic()-started,3),new_saved=len(saved & getattr(db,'job_new_keys',set())),updated_saved=len(saved-getattr(db,'job_new_keys',set())),processed=search['summary'].get('detail',0)+search['summary'].get('already_acquired',0),last_saved_at=getattr(state,'new_last_saved_at',None),saved=len(saved),unsaved=unsaved,acquisition_ids_pending=len(acquisition_pending),issues=issues[-100:])
    if unsaved:
        audit.add('database','unsaved_listings','ERROR',{'stage':'stop.final_save' if web.cancel_event.is_set() else 'search.final_save','unsaved':unsaved,'retained_in_server_memory':True})
    audit.add('map','data_ready','INFO',{'confirmed_units':len(units),'colored_points':sum(has_point(r) for r in units.values()),'render_deferred_until_saved_data_load':False})
    screen['status'].info('検索が終了しました。詳細作業ログをSupabaseへ保存しています。')
    audit.add('search','finish','INFO',{'status':search['status'],'summary':search['summary']})
    audit.persist(db,force=True)
    search['summary']['diagnostics']={'search_id':audit.search_id,'events':audit.count,'persisted_events':audit.persisted_events}
    search['finished_at']=utc_now()
    try: db.save_search(search)
    except AppError as exc:
        issues.append('検索履歴｜'+str(exc));search['summary']['issues']=issues
        if search['status']!='cancelled':search['status']='partial'
    screen['bar'].progress(1.,text='検索処理が終了しました')
    state.new_units=list(units.values())
    state.new_search=search
    state.new_saved_keys=list(saved)
    return search


# Detailed diagnostics are independent of UI and retained in Supabase search-log chunks.
DIAG_CONTEXT=threading.local()

DIAG_ADVICE={
 'timeout':('接続または応答の時間超過','同じURLをブラウザで確認し、サービス障害とStreamlitからの通信制限を切り分ける。正常時の応答時間を測ってからタイムアウトと同時接続数を調整する。'),
 'http_403':('公開先がアクセスを拒否','通信試行ログの経路番号を確認する。プロキシ設定・認証・HTTPS接続と経路切替後のHTTP状態を比較する。403の解消は成功応答で確認する。'),
 'http_429':('取得先の要求回数制限','同じ自治体・ページの重複要求を減らし、キャッシュと取得間隔を導入する。Retry-Afterがあればその値に従う。'),
 'http_404':('対象のURL・データが存在しない','掲載終了とAPI・タイルURL変更を区別する。現行の公開ページ・公式仕様とURLを照合する。'),
 'http_5xx':('取得先のサーバーエラー','発生時刻とURLを確認して障害を切り分け、復旧後に失敗したタスクだけを再検証する。'),
 'network':('接続・DNS・TLSの問題の可能性','同一ホストの複数URLと別ホストの結果を比較する。StreamlitログでDNS/TLS・外向き通信を確認する。'),
 'parse':('掲載情報の形式を読み取れない','HTTP成功時のページタイトル・ハッシュ・検出件数と現行HTML/JSONを照合し、項目名・セレクタ・ページ送りを修正して同じ例で再検証する。'),
 'structure':('構造の掲載情報','実際の構造表記とSRC判定を照合する。RC等の正しい除外と表記ゆれの誤判定を分ける。'),
 'age':('築年数の掲載情報','築年月の元表記・解析値・実行日を照合する。築20年超は仕様上の除外であり取得失敗とは扱わない。'),
 'layout':('間取り条件または表記の不一致','間取りの元表記を確認し、空白・全角・括弧等を正規化する。対象外間取りは仕様上の除外とする。'),
 'address':('地名・住所の照合で不一致','丁目の漢数字・数字、町名表記、市区名の変換を照合する。実際の掲載座標との比較で誤除外がないか確認する。'),
 'location':('掲載座標・住所の位置を確認できない','掲載地図・JSON-LD・住所検索の各候補と表示範囲を比較する。番地や掲載座標がない物件を架空座標で補完しない。'),
 'outside':('物件が検索開始時の表示範囲外','ログの固定表示範囲と掲載座標を比較する。範囲内の物件を落とす場合は座標順序・境界の判定を修正する。'),
 'save':('Supabaseの保存または保存確認の失敗','物件確認数と保存確認数を比較し、キー・テーブル・権限・HTTP応答を確認する。未保存物件の再保存で復旧を検証する。'),
 'empty':('取得元の一覧に候補がない','地名・自治体コード・検索パラメータをログのURLで照合する。手動検索の件数と比較し、真の0件と検索条件/解析の誤りを区別する。'),
 'location_unverified':('掲載座標はあるが住所照合は未判定','地名照合サービスの失敗ログを確認する。掲載座標と実際の建物位置を手動で照合し、未判定を位置確認済みとして評価しない。'),
 'layout_assumed':('掲載間取りを単独検索の指定値で補完','検索条件・公開フォームの値と結果一覧を照合する。掲載間取りを確認できたら優先し、異なる明示間取りは除外する。補完済みを掲載確認済みとは扱わない。'),
 'request_spacing':('取得元への同時接続と頻度を調整','物件サイトごとの同時通信は2件、通信開始は最低1秒間隔。取得件数・ページ数には上限を設けない。'),
 'session_entry':('同じ経路で公開トップページを確認','応答状態・Cookie件数と再試行結果を比較する。Cookieの値は保存しない。'),
 'session_retry':('公開ページ閲覧後にセッション付き再試行','同じプロキシ・Cookieを使い、実際に訪れたページをRefererとして送る。改善したか後続応答で確認する。'),
 'route_cooldown':('拒否された経路またはRetry-Afterの待機','残り待機時間を確認する。403の経路とURLは60秒休止し、他の設定済み経路があれば試す。'),
 'form_alternative':('別の公開ページで間取り条件を確認','成功した公開ページのフォーム名と値を確認する。条件が確認できなければ物件取得を開始しない。'),
 'http_challenge':('物件本文ではなくアクセス検証画面','実際のHTTP状態・ページタイトル・経路番号を比較する。検証画面を物件ページとして解析しない。'),
 'transport_config':('取得経路の設定','プロキシ未設定ならStreamlit SecretsにRENTAL_HTTP_PROXIESを設定する。物件サイトだけに適用する。'),
 'transport_attempt':('実際の通信試行','経路番号・HTTP状態・本文バイト数を比較し、取得成功した経路を確認する。'),
 'proxy_switch':('別のプロキシ経路へ切替','切替後のHTTP状態を確認する。全経路で403なら取得可能とは表示しない。'),
 'proxy_error':('プロキシ経路の接続失敗','プロキシの契約状態、ホスト・ポート・認証情報、HTTPS CONNECT対応を確認する。'),
 'http_retry':('一時的なHTTPエラーを再試行','Retry-Afterと再試行後の状態を確認する。繰り返す場合は取得元またはプロキシの接続状況を調べる。'),
 'provider_unavailable':('取得元の検索条件を取得できない','通信試行ログを確認する。403が全経路で続く場合はプロキシ接続先と取得元へのアクセス可否を確認する。'),
 'layout_unsupported':('取得元に単独検索条件がない','この間取りは検索未実施。取得元の公開フォームに対応する間取りがあるか確認する。'),
 'partial_retained':('取得できた募集情報を保存対象に保持','未確認項目があっても取得済みの家賃・間取り・地域を反映する。建物座標と町丁目の概算を区別する。'),
 'detail_optional_error':('詳細の追加確認でエラー・一覧情報は保持','一覧から取得できた募集項目とSupabase保存確認を見る。詳細エラーを募集全体の除外理由にしない。'),
 'location_pending':('条件に合う募集だが建物位置が未確認','記録したURLと掲載項目を用いて掲載地図・正確な番地を確認する。地図の250m区画へ町丁目代表点を入れない。位置を確認した後に再取得・保存する。'),
 'map_link_result':('掲載ページから地図ページを追加確認','座標の出所と掲載住所を照合する。町丁目の代表点を建物座標として扱わない。'),
 'http_empty':('HTTP 202の空応答で掲載データが未到着','公開ページを手動で確認し、取得元のアクセス条件と公開API/データ提供を確認する。空本文をHTML解析失敗や間取り欠落と扱わない。'),
 'image_georef_missing':('地図画像の基準座標・縮尺が不明','画像URLや埋め込み地図の中心座標・ズームを確認する。基準のない画像から緯度経度を捏造しない。'),
 'image_pin_result':('地図画像の物件ピン認識結果','認識ピクセル位置・地図基準座標・ズーム・逆投影後の座標を確認する。'),
 'position_pending':('掲載地図の位置再取得が未完了','掲載URL、地図リンク、画像認識・HTTPエラーのイベントを確認する。元の募集データは保持する。'),
 'position_repaired':('掲載地図から位置と住所を更新','position_source_urlと推定住所を比較する。番地を読み取れていない場合は町丁目までの推定として扱う。'),
 'reverse_address_pending':('地図位置は保持・逆算住所は未確認','国土地理院の通信結果と市区町村コード表を確認する。逆算住所の未確認で地図位置を捨てない。'),
 'map_multiple_candidates':('物件の座標候補が競合','候補の出所を照合する。店や地図中心の座標で代用しない。'),
 'map_address_difference':('掲載住所と逆算町字が異なる','掲載地図の座標と逆算住所を別項目で確認する。番地の一致とは扱わない。'),
 'unknown':('現時点で理由を特定できない','前後の通信・解析・除外イベントを照合する。再現URLと読取項目を追加してから修正する。原因を断定しない。')}

DIAG_ADVICE.update({'address_tile_empty':('GSIの住所タイルにデータがない','HTTP 404とNoSuchKeyが両方確認された空タイル。取得できた周辺タイルの同一町丁目の住所候補を比較する。その他の通信失敗と区別する。'),'listing_rejected':('物件の除外理由と工程・取得値・期待値','failed_stage/observed/expected/evidenceで条件不一致と読取・住所推定失敗を区別する。'),'listing_failed':('物件の通信・解析処理に失敗','HTTP状態・例外型・直前の工程を確認する。原因未確定を確定扱いしない。')})

def diagnosis_code(message):
    text=str(message)
    if '取得経路の待機中' in text:return 'route_cooldown'
    if 'アクセス検証画面' in text:return 'http_challenge'
    if 'プロキシ接続失敗' in text:return 'proxy_error'
    if 'HTTP 202' in text and '0バイト' in text:return 'http_empty'
    for needle,code in [('タイムアウト','timeout'),('HTTP 403','http_403'),('HTTP 429','http_429'),('HTTP 404','http_404')]:
        if needle in text:return code
    if re.search(r'HTTP 5\d\d',text):return 'http_5xx'
    if 'Supabase' in text or '保存' in text or '権限' in text:return 'save'
    if any(x in text for x in ('形式','読み取','JSON','ページ送り')):return 'parse'
    if any(x in text for x in ('接続','通信','DNS')):return 'network'
    return 'unknown'

class AuditLog:
    def __init__(self,conditions,secrets=()):
        fd,self.path=tempfile.mkstemp(prefix='sumai-log-',suffix='.jsonl');os.close(fd)
        self.lock=threading.RLock();self.count=0;self.offset=0;self.chunk=0;self.last_save=0.;self.persisted_events=0
        self.storage_error=None;self.search_id=hashlib.sha256(os.urandom(32)).hexdigest();self.secrets=tuple(str(v) for v in secrets if v)
        self.add('search','start','INFO',{'build':BUILD,'bounds':conditions.get('bounds'),'providers':conditions.get('providers'),'filters':{'structure':None,'max_age':None,'layouts':list(LAYOUTS),'suumo':{'building_type':'マンション','max_age':15,'layout_groups':[list(x) for x in SUUMO_LAYOUT_GROUPS]}},'property_limit':None})
    def clean(self,value):
        if isinstance(value,dict):return {str(k):('[REDACTED]' if re.search(r'^(?:key|apikey|api_key|token|access_token|authorization|cookie|password|secret|SUPABASE_.*KEY)$',str(k),re.I) else self.clean(v)) for k,v in value.items()}
        if isinstance(value,(list,tuple)):return [self.clean(x) for x in value]
        if isinstance(value,str):
            for secret in self.secrets:value=value.replace(secret,'[REDACTED]')
            value=re.sub(r'(https?://)[^/\s@]+@',r'\1[REDACTED]@',value)
            value=re.sub(r'(sb_secret_[\w-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)', '[REDACTED]',value)
            value=re.sub(r'(?i)([?&](?:apikey|key|token|password|secret)=)[^&\s]+',r'\1[REDACTED]',value)
            return value
        if isinstance(value,float) and not math.isfinite(value):return str(value)
        return value
    def add(self,stage,code,level='INFO',details=None):
        cause,advice=DIAG_ADVICE.get(code,('進行・確認結果',''))
        with self.lock:
            self.count+=1
            context=getattr(DIAG_CONTEXT,'scope',{})
            event=self.clean(dict(seq=self.count,time=utc_now(),search_id=self.search_id,stage=stage,code=code,level=level,
                                 context=context,details=details or {},observed=cause,improvement=advice))
            with open(self.path,'a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False,allow_nan=False)+'\n')
    def records(self):
        with self.lock:
            with open(self.path,encoding='utf-8') as f:return [json.loads(line) for line in f if line.strip()]
    def persist(self,db,force=False):
        if not force and time.monotonic()-self.last_save<30:return
        self.last_save=time.monotonic()
        while True:
            with self.lock:
                with open(self.path,encoding='utf-8') as f:
                    f.seek(self.offset);events=[]
                    for _ in range(200):
                        line=f.readline()
                        if not line:break
                        events.append(json.loads(line))
                    end=f.tell()
            if not events:
                self.storage_error=None;return
            row=dict(id=f'{self.search_id}.log.{self.chunk:08d}',status='diagnostic_log',started_at=events[0]['time'],finished_at=events[-1]['time'],
                     conditions={'search_id':self.search_id,'build':BUILD,'chunk':self.chunk},summary={'events':events})
            try:
                db._saving_audit=True;db.save_search(row)
            except Exception as exc:
                self.storage_error=str(exc) if isinstance(exc,AppError) else type(exc).__name__
                self.add('log_storage','save','ERROR',{'message':self.storage_error,'local_download_available':True});return
            finally:db._saving_audit=False
            self.offset=end;self.chunk+=1;self.persisted_events+=len(events)
            if not force:
                self.storage_error=None;return


def begin_listing(web,provider,url):
    if not hasattr(web,'local'):web.local=threading.local()
    web.local.listing_evidence=[]
    DIAG_CONTEXT.scope={**getattr(DIAG_CONTEXT,'scope',{}),'provider':provider,'listing_url':url}

def reject_listing(web,provider,url,stage,category,reason,observed=None,expected=None):
    evidence=list(getattr(getattr(web,'local',None),'listing_evidence',[]))
    if stage=='map.address_inference':
        stages={'suumo_marker_invalid':'map.marker_json','suumo_marker_unresolved':'map.room_marker',
                'map_multiple_candidates':'map.image_candidates','image_georef_missing':'map.image_metadata',
                'image_pin_error':'map.image_decode','image_pin_result':'map.image_pin',
                'outside':'map.bounds','map_address_difference':'address.town_match',
                'homes_map_image_candidates':'map.image_candidates','map_address_unresolved':'address.residential_candidates'}
        for item in reversed(evidence):
            code=item['code'];detail=item['details']
            if code not in stages:continue
            if code=='image_pin_result' and detail.get('point'):continue
            if code=='homes_map_image_candidates' and (detail.get('usable_candidates') or detail.get('in_bounds_candidates')):continue
            stage=detail.get('failed_stage') or stages[code];observed=detail
            if code=='map_address_unresolved' and detail.get('tile_errors'):stage='address.tiles';category='acquisition_failure'
            reason=detail.get('reason') or {'suumo_marker_unresolved':'物件マーカーを一意に取得できない','homes_map_image_candidates':'範囲内の地図画像ピン候補を取得できない','outside':'物件マーカーが検索範囲外','map_multiple_candidates':'物件ピン候補が複数ある','map_address_difference':'推定住所が対象町域と一致しない'}.get(code,reason)
            if code in ('outside','map_address_difference'):category='condition'
            break
    trace(web,'listing_rejected',{'provider':provider,'url':url,'decision':'excluded','failed_stage':stage,
          'category':category,'reason':reason,'observed':observed,'expected':expected,'evidence':evidence},'WARNING','rejection')
    return None

def exclusion_rows(events):
    return [{'時刻UTC':e.get('time'),'取得元':e.get('details',{}).get('provider'),'物件URL':e.get('details',{}).get('url'),
             '判定':e.get('details',{}).get('decision'),'工程':e.get('details',{}).get('failed_stage'),
             '分類':e.get('details',{}).get('category'),'理由':e.get('details',{}).get('reason'),
             '取得値':json.dumps(e.get('details',{}).get('observed'),ensure_ascii=False),
             '期待値':json.dumps(e.get('details',{}).get('expected'),ensure_ascii=False)}
            for e in events if e.get('code') in ('listing_rejected','listing_failed')]


def trace(web,code,details=None,level='INFO',stage='parser'):
    local=getattr(web,'local',None)
    if local is not None and hasattr(local,'listing_evidence') and code not in ('listing_rejected','listing_failed') and (level in ('WARNING','ERROR') or code in ('reverse_result','image_pin_result','homes_map_image_candidates')):
        local.listing_evidence.append({'code':code,'stage':stage,'details':details or {}})
        local.listing_evidence=local.listing_evidence[-20:]
    audit=getattr(web,'audit',None) or getattr(DIAG_CONTEXT,'audit',None)
    if audit:audit.add(stage,code,level,details)


def diagnostic_report(events):
    counts={};examples={}
    for e in events:
        if e.get('code') in DIAG_ADVICE:
            c=e['code'];counts[c]=counts.get(c,0)+1;examples.setdefault(c,e)
    lines=['住まいコンパス 詳細作業ログ・改善レポート',f"アプリ版：{next((e.get('details',{}).get('build') for e in events if e.get('code')=='start'),BUILD)}｜イベント数：{len(events)}",
           '事実と改善案を分けています。改善案は調査・修正候補であり、原因の確定や修正済みを意味しません。',
           '取得精度の評価には正解データとの照合が必要です。このログの確認/保存件数だけでは正確性・網羅性を保証しません。','', '原因別の観測と次の改善：']
    for code,count in sorted(counts.items(),key=lambda x:-x[1]):
        cause,advice=DIAG_ADVICE[code];lines.extend([f'・{cause}：{count}イベント',f'  改善候補：{advice}',f'  根拠例：{json.dumps(examples[code].get("details",{}),ensure_ascii=False)}'])
    lines.extend(['','改善後の確認手順：同じ固定範囲・取得元で再検索し、地名数、候補数、詳細確認数、理由別除外数、座標未判定数、保存確認数を比較する。',
                  '手動検索で見つかる実在物件のURLを数件用意し、このログで取得・除外・未検出の経路を確認する。','', '詳細イベント（UTC・通番・取得元/地名・通信/解析/除外・改善案）：'])
    for e in events:lines.append(json.dumps(e,ensure_ascii=False))
    return ('\n'.join(lines)+'\n').encode('utf-8-sig')



def log_metrics(events):
    counts={}
    for e in events:
        code=e.get('code','unknown');counts[code]=counts.get(code,0)+1
    finishes=[e for e in events if e.get('code')=='finish']
    summary=finishes[-1].get('details',{}).get('summary',{}) if finishes else {}
    for key in ('candidate','detail','rejected','confirmed','saved'):counts['件数:'+key]=summary.get(key,0)
    return counts


def compare_logs(old,new):
    a,b=log_metrics(old),log_metrics(new)
    return [{'項目':key,'前回':a.get(key,0),'今回':b.get(key,0),'差分':b.get(key,0)-a.get(key,0)} for key in sorted(set(a)|set(b))]

def diagnostic_downloads(events,prefix='current'):
    if not events:return
    export_key=(events[0].get('search_id'),len(events),events[-1].get('seq'))
    export=st.session_state.get(prefix+'_log_export')
    if not export or export['key']!=export_key:
        export={'key':export_key,'txt':diagnostic_report(events),'json':json.dumps({'schema':1,'events':events},ensure_ascii=False,separators=(',',':')).encode('utf-8')};export['decisions']=exclusion_rows(events)
        reason_output=io.StringIO()
        if export['decisions']:
            reason_writer=csv.DictWriter(reason_output,fieldnames=list(export['decisions'][0]));reason_writer.writeheader();reason_writer.writerows(export['decisions'])
        export['reasons_csv']=reason_output.getvalue().encode('utf-8-sig');export['preview']=export['txt'].decode('utf-8-sig').split('詳細イベント')[0];st.session_state[prefix+'_log_export']=export
    st.download_button('詳細作業ログ・改善案をTXTでダウンロード',export['txt'],'sumai_work_log.txt','text/plain',key=prefix+'_txt',on_click='ignore')
    st.download_button('解析用の詳細ログをJSONでダウンロード',export['json'],
                       'sumai_work_log.json','application/json',key=prefix+'_json',on_click='ignore')
    decisions=export['decisions']
    if decisions:
        st.download_button('物件ごとの除外・取得失敗理由をCSVでダウンロード',export['reasons_csv'],'sumai_exclusion_reasons.csv','text/csv',key=prefix+'_reasons',on_click='ignore')
    with st.expander('作業ログの原因別集計と改善案'):
        if decisions:st.dataframe(decisions[-100:],hide_index=True)
        st.text(export['preview'])
        st.caption('全イベントはダウンロードに含まれます。画面には最新20イベントを表示します。')
        st.dataframe([{'時刻UTC':e['time'],'工程':e['stage'],'理由':e['code'],'取得元・地名':str(e.get('context',{})),
                       '詳細':str(e.get('details',{})),'改善案':e.get('improvement','')} for e in events[-20:]],hide_index=True)

def physical_units(rows):
    """Keep each acquired listing; do not merge by address, price, room size or location."""
    return list(rows)


DISPLAY_REASONS={
 'invalid':'保存レコードのURL・家賃等が不正',
 'bounds':'現在の地図範囲外（位置不明なら検索範囲の重なりで判定）',
 'location_missing':'位置未確認：取得情報は保持・地図配置は保留',
 'address':'保存済みの地図由来住所を読み込み時に再座標化して表示',
 'unpriced':'家賃未確認：1募集の灰色の点として表示',
 'town':'1募集を1点として表示（町丁目の位置・建物位置未確認）',
 'building':'1募集を掲載地図等から取得した座標の1点として表示'}


def display_pipeline(units,bounds,load=None):
    """All layouts and all prices, one point per listing; no spatial or price aggregation."""
    rows=[];records=[];points=[];counts={k:0 for k in DISPLAY_REASONS}
    for r in units:
        if not valid_unit(r):reason='invalid'
        elif not listing_in_bounds(r,bounds):reason='bounds'
        else:
            rows.append(r)
            if not has_point(r):reason='location_missing'
            else:
                points.append((r['latitude'],r['longitude']))
                reason='unpriced' if not r.get('rent') else 'town' if r.get('coordinate_precision')=='town' else 'address' if r.get('coordinate_precision')=='address' else 'building'
        counts[reason]+=1
        records.append({'key':r.get('key'),'title':r.get('title'),'provider':r.get('provider'),'listing_url':r.get('listing_url'),
            'layout':r.get('layout'),'rent':r.get('rent'),'fees':r.get('fees'),'monthly':monthly_price(r) if r.get('rent') else None,
            'region':r.get('region_label') or r.get('address'),'latitude':r.get('latitude'),'longitude':r.get('longitude'),
            'coordinate_precision':r.get('coordinate_precision'),'location_method':r.get('location_method'),'inferred_address':r.get('inferred_address'),'position_source_url':r.get('position_source_url'),
            'reason_code':reason,'reason':DISPLAY_REASONS[reason],'merged_into':None})
    report={'schema':2,'build':BUILD,'time':utc_now(),'filters':{'bounds':bounds,'layouts':None,'monthly_limit':None,'structure':None,'age':None},
        'aggregation':False,'load':load,'stages':{'loaded':len(units),'in_bounds':len(rows),'individual_points':len(points)},
        'reason_counts':counts,'map':{'markers_total':len(points),'colored_points':counts['town']+counts['address']+counts['building'],
        'gray_points':counts['unpriced'],'unique_positions':len(set(points)), 'overlapping_points':len(points)-len(set(points)),
        'renderer':'Canvas','aggregation':False,'unconfirmed_positions':sum(not has_point(r) or r.get('coordinate_precision') in ('town','address') for r in rows)}, 'records':records,
        'improvements':['位置未確認の場合は掲載URL・住所・掲載地図の読取結果を確認して座標取得を改善する。取得済み募集は捨てない。',
            '町丁目しか分からない募集はその位置を明記する。同じ座標の点は重なるが、件数をまとめたり実在しない位置へ散らしたりしない。',
            '家賃未確認の場合は一覧・詳細の金額の読取箇所を確認する。管理費未確認なら読み取れた家賃で色分けする。']}
    return rows,rows,[],report


def display_diagnostic_downloads(report):
    stages=report['stages'];mapping=report['map']
    st.caption(f"読み込んだ募集 {stages['loaded']}件｜現在の範囲 {stages['in_bounds']}件｜1件ずつ地図へ描画 {mapping['markers_total']}件｜位置未確認 {mapping['unconfirmed_positions']}件")
    st.caption(f"家賃帯で色付け {mapping['colored_points']}点｜家賃未確認の灰色 {mapping['gray_points']}点｜同じ座標への重なり {mapping['overlapping_points']}点。月額上限・間取りによる除外、募集の集約は行いません。")
    with st.expander('表示が少ない原因・全募集の診断ログ'):
        st.dataframe([{'理由':DISPLAY_REASONS[k],'募集件数':v} for k,v in report['reason_counts'].items()],hide_index=True)
        text=['住まいコンパス 1募集1点の表示診断',json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False,indent=2),'募集ごとの判定：']
        text.extend(json.dumps(r,ensure_ascii=False) for r in report['records'])
        st.download_button('表示原因ログをTXTでダウンロード','\n'.join(text).encode('utf-8-sig'),'sumai_display_log.txt','text/plain',key='display_txt',on_click='ignore')
        st.download_button('表示原因ログをJSONでダウンロード',json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'),'sumai_display_log.json','application/json',key='display_json',on_click='ignore')
        output=io.StringIO();writer=csv.DictWriter(output,fieldnames=list(report['records'][0]) if report['records'] else ['key','reason']);writer.writeheader();writer.writerows(report['records'])
        st.download_button('全募集の表示判定をCSVでダウンロード',output.getvalue().encode('utf-8-sig'),'sumai_display_decisions.csv','text/csv',key='display_csv',on_click='ignore')
        for advice in report['improvements']:st.write('・'+advice)


def rent_color(price):
    """Monthly rent plus management fees; each threshold belongs to the lower band."""
    return COLORS[sum(price>cut for cut in BANDS)]


def mesh(rows,center,radius,interpolate=False,bounds=None):
    """No cell averages or town aggregation: each listing has its own point."""
    return []



class MonotoneBase(MacroElement):
    """Desaturate only the base tile pane; rent colors remain untouched."""
    _template=Template("""{% macro script(this, kwargs) %}
    {{ this._parent.get_name() }}.getPane('monotoneBase').style.filter = 'grayscale(1)';
    {{ this._parent.get_name() }}.getPane('monotoneBase').style.webkitFilter = 'grayscale(1)';
    {{ this._parent.get_name() }}.getContainer().style.backgroundColor = '#ececec';
    {% endmacro %}""")
    def __init__(self):
        super().__init__();self._name='MonotoneBase'

def rental_map(rows,center,radius,cells,facilities):
    m=folium.Map(location=center,zoom_start=15,tiles=None,control_scale=True,prefer_canvas=True)
    folium.map.CustomPane('monotoneBase',z_index=200,pointer_events=False).add_to(m)
    MonotoneBase().add_to(m)
    folium.TileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png',
        attr='国土地理院',name='駅名・地名のモノトーン地図',max_zoom=18,pane='monotoneBase').add_to(m)
    folium.map.CustomPane('rentIndividualPane',z_index=410,pointer_events=False).add_to(m)
    # Base-map station and town labels stay visible through translucent rent areas.
    # Major station names also have persistent white-backed labels above the colors.
    for name,point in STATIONS.items():
        folium.CircleMarker(point,radius=2,color='#303030',weight=1,fill=True,fill_color='#ffffff',fill_opacity=1,
            tooltip=folium.Tooltip(html.escape(name)+'駅',permanent=True,direction='top',
                style='background:rgba(255,255,255,.94);color:#303030;border:0;font-size:12px;font-weight:700;box-shadow:none;padding:2px 4px;')).add_to(m)
    rental_features(rows,cells,facilities).add_to(m)
    return m


def listing_card(r,persisted):
    price=f"{float(r.get('rent') or 0)/10000:g}万円 / 月" if r.get('rent') else '家賃未確認'
    return '<div class="unit"><h3>'+html.escape(r.get('address') or '住所未確認')+'</h3><b>'+price+'</b> · '+html.escape(r.get('layout') or '間取り未確認')+'<p>住所は地図からの推定値です。<br>データ取得日時：'+html.escape(acquisition_time_jst(r.get('fetched_at')))+'</p></div>'


class IndividualRentPoints(MacroElement):
    """One Canvas renderer; one noninteractive colored point per acquired listing."""
    _template=Template("""{% macro script(this, kwargs) %}
    var {{ this.get_name() }}_data = {{ this.payload }};
    var {{ this.get_name() }}_map = {{ this._parent._parent.get_name() }};
    var {{ this.get_name() }}_renderer = {{ this.get_name() }}_map._individualRentCanvas;
    if (!{{ this.get_name() }}_renderer) {
        {{ this.get_name() }}_renderer = L.canvas({padding:0.3,pane:'rentIndividualPane'});
        {{ this.get_name() }}_map._individualRentCanvas = {{ this.get_name() }}_renderer;
    }
    {{ this.get_name() }}_data.forEach(function(item) {
        L.circleMarker([item.lat,item.lng],{renderer:{{ this.get_name() }}_renderer,
            radius:6.5,color:'#ffffff',weight:item.approx?1.8:1.0,interactive:false,
            dashArray:item.approx?'2,2':null,fill:true,fillColor:item.color,fillOpacity:0.96})
        .addTo({{ this._parent.get_name() }});
    });
    {% endmacro %}""")
    def __init__(self,rows):
        super().__init__();self._name='IndividualRentPoints';self.items=[]
        for r in rows:
            if not has_point(r):continue
            self.items.append({'key':r['key'],'lat':r['latitude'],'lng':r['longitude'],
                'color':rent_color(monthly_price(r)) if r.get('rent') else '#777777',
                'approx':r.get('coordinate_precision') in ('town','address')})
        self.payload=json.dumps(self.items,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def rental_features(rows,cells,facilities):
    m=folium.FeatureGroup(name='1募集1点・家賃帯')
    IndividualRentPoints(rows).add_to(m)
    for f in facilities:
        folium.CircleMarker((f['lat'],f['lng']),radius=6,color='#fff',fill=True,fill_color='#555555',fill_opacity=1,tooltip=html.escape(f['name'])).add_to(m)
    return m


def commute(origin,destination):
    edges={name:[] for name in STATIONS}
    for line,stations in LINES.items():
        for a,b in zip(stations,stations[1:]):
            minutes=meters(STATIONS[a],STATIONS[b])*1.15/1000/40*60
            edges[a].append((b,line,minutes));edges[b].append((a,line,minutes))
    todo=[(0,origin,'',0,())];seen=set()
    while todo:
        minutes,node,last,changes,path=heapq.heappop(todo)
        if (node,last) in seen: continue
        seen.add((node,last))
        if node==destination: return dict(minutes=minutes,transfers=changes,path=path)
        for next_node,line,duration in edges[node]:
            change=int(bool(last) and last!=line)
            heapq.heappush(todo,(minutes+duration+5*change,next_node,line,changes+change,path+((node,next_node,line),)))
    raise AppError('登録路線では経路を計算できません。')


def fetch_facilities(center,kind):
    tags={'スーパー':'shop=supermarket','コンビニ':'shop=convenience','公園':'leisure=park','病院':'amenity~"hospital|clinic"'}
    query=f'[out:json][timeout:20];nwr[{tags[kind]}](around:1000,{center[0]},{center[1]});out center tags;'
    try:
        r=requests.post('https://overpass-api.de/api/interpreter',data={'data':query},timeout=(4,25),allow_redirects=False)
        r.raise_for_status();data=r.json()
    except (requests.RequestException,ValueError): raise AppError('周辺施設を取得できません。時間を置いて再試行してください。') from None
    out=[]
    for el in data.get('elements',[]):
        p=el.get('center') or el;name=el.get('tags',{}).get('name')
        if name and 'lat' in p and 'lon' in p: out.append(dict(name=name,lat=p['lat'],lng=p['lon']))
    return out


def csv_bytes(rows):
    keys=['property_id','rent','layout','address','fetched_at'];file=io.StringIO();writer=csv.DictWriter(file,fieldnames=keys);writer.writeheader()
    for row in rows:
        compact=compact_saved_listing(row)
        if compact:writer.writerow(compact)
    return file.getvalue().encode('utf-8-sig')


def read_csv(content):
    try:
        rows=[]
        for raw in csv.DictReader(io.StringIO(content.decode('utf-8-sig'))):
            row={'property_id':normal(raw.get('property_id')) or None,'rent':int(raw['rent']),'layout':normal(raw['layout']),'address':normal(raw['address']),'fetched_at':normal(raw.get('fetched_at')) or None}
            compact=compact_saved_listing(row)
            if not compact:raise ValueError()
            key=compact_listing_key(compact);rows.append({'key':key,'title':compact['address'],**compact,'fetched_at':compact.get('fetched_at'),'fees':0,'loaded_from_compact_storage':True})
        if not rows:raise ValueError()
        return rows
    except (UnicodeError,ValueError,KeyError,TypeError,csv.Error):raise AppError('家賃・間取り・住所と、任意の取得日時（fetched_at）列を持つCSVを指定してください。') from None


def geocode_saved_address(web,address):
    data=web.fetch('https://msearch.gsi.go.jp/address-search/AddressSearch',params={'q':address}).json()
    if not isinstance(data,list):return None
    wanted=address_key(address);candidates=[]
    for item in data:
        try:
            lng,lat=map(float,item['geometry']['coordinates']);title=normal(item.get('properties',{}).get('title',''))
            if not (34<=lat<=37 and 138<=lng<=141):continue
            score=3 if address_key(title)==wanted else 2 if wanted in address_key(title) or address_key(title) in wanted else 1
            candidates.append((score,len(title),lat,lng,title))
        except (KeyError,ValueError,TypeError):continue
    if not candidates:return None
    _,_,lat,lng,title=max(candidates,key=lambda x:(x[0],x[1]))
    return lat,lng,title


def geocode_saved_address_cached(web,address):
    memory=getattr(web,'address_points',None)
    cached=cached_saved_position(memory,address)
    if cached:return cached
    if not memory:return geocode_saved_address(web,address)
    key=normal(address)
    with memory['lock']:
        flight=memory['flights'].get(key);owner=flight is None
        if owner:flight=futures.Future();memory['flights'][key]=flight
    if not owner:
        while not flight.done():web.pause(.05)
        try:return flight.result()
        except SearchCancelled:
            web.check_cancel()
            with memory['lock']:
                if memory['flights'].get(key) is flight:memory['flights'].pop(key,None)
            return geocode_saved_address_cached(web,address)
    try:
        point=geocode_saved_address(web,address)
        remember_saved_position(memory,address,point);flight.set_result(point);return point
    except BaseException as exc:
        flight.set_exception(exc);raise
    finally:
        with memory['lock']:
            if memory['flights'].get(key) is flight:memory['flights'].pop(key,None)


def hydrated_listing(row,point):
    if not point:return dict(row,coordinate_precision='unknown',location_method='保存住所の地図配置を確認できない')
    lat,lng,title=point
    return dict(row,latitude=lat,longitude=lng,coordinate_precision='address',location_method='保存済み地図由来住所を読み込み時に住所検索',map_address=title,address_match='保存住所から再配置')


def hydrate_saved_units(rows,bounds,cache=None,web=None,notify=None):
    cache=cache if isinstance(cache,dict) else {};addresses=list(dict.fromkeys(r['address'] for r in rows if r.get('address')));web=web or PublicWeb()
    def locate_address(address):
        web.check_cancel()
        if cache.get(address):return cache[address]
        return geocode_saved_address_cached(web,address)
    pending=[]
    for address in addresses:
        web.check_cancel()
        memory=getattr(web,'address_points',None)
        point=cache.get(address) or (cached_saved_position(memory,address) if memory else None)
        if point:
            cache[address]=point
            if notify:notify(address,point,None)
        else:pending.append(address)
    for address,point,error in bounded_results(pending,locate_address,workers=2,stop_event=web.cancel_event):
        if address is None:continue
        cache[address]=point if not error else None
        if notify:notify(address,cache[address],error)
    return [hydrated_listing(row,cache.get(row.get('address'))) for row in rows],cache


class SavedLoadJob:
    """DB rows become available independently of geocoding; no Streamlit in worker."""
    def __init__(self,db,bounds,cache=None):
        self.db=db;self.bounds=bounds;self.token=hashlib.sha256(os.urandom(32)).hexdigest();self.server_key=manual_registry_key(db)
        self.lock=threading.RLock();self.cancel_event=threading.Event();self.rows={};self.cache={}  # Session points lack timestamps; reuse only the TTL-controlled server cache.
        self.phase='database';self.finished=False;self.error='';self.complete_db=False;self.pages=0;self.total_addresses=0;self.done_addresses=0;self.placed=0;self.persistent_cache_hits=0;self.geocoded_addresses=0;self.diagnostic={};self.started=time.monotonic();self.ended=None
        self.thread=threading.Thread(target=self.run,daemon=True,name='housing-saved-loader')
    def snapshot(self):
        with self.lock:
            end=self.ended if self.ended is not None else time.monotonic()
            return {'token':self.token,'phase':self.phase,'finished':self.finished,'error':self.error,'complete_db':self.complete_db,
                'rows':len(self.rows),'pages':self.pages,'addresses':self.total_addresses,'done_addresses':self.done_addresses,'placed':self.placed,
                'persistent_cache_hits':self.persistent_cache_hits,'geocoded_addresses':self.geocoded_addresses,
                'elapsed':round(end-self.started,1),'stopping':self.cancel_event.is_set()}
    def export(self):
        with self.lock:return [dict(r) for r in self.rows.values()],dict(self.cache),dict(self.diagnostic)
    def request_stop(self):self.cancel_event.set()
    def run(self):
        web=PublicWeb();web.cancel_event=self.cancel_event;web.address_points=getattr(self.db,'address_points',None)
        def page(rows,diag):
            with self.lock:
                for row in rows:
                    point=self.cache.get(row['address']) or cached_saved_position(web.address_points,row['address'])
                    if point:self.cache[row['address']]=point
                    self.rows[row['key']]=hydrated_listing(row,point)
                self.pages+=1;self.placed=sum(has_point(r) for r in self.rows.values());self.diagnostic=diag
        try:
            stored=self.db.load_units(None,on_progress=page,cancel_event=self.cancel_event)
            addresses=list(dict.fromkeys(r['address'] for r in stored));by_address={a:[] for a in addresses}
            # Persistent cache is loaded in one paged DB pass.  This avoids repeating
            # GSI address searches on every click after the first successful placement.
            persistent={}
            try:persistent=self.db.load_address_points(addresses,self.cancel_event)
            except AppError as exc:
                with self.lock:self.diagnostic['persistent_address_cache_load_error']=str(exc)
            for address,point in persistent.items():
                self.cache[address]=point
                remember_saved_position(web.address_points,address,point)
            with self.lock:
                self.persistent_cache_hits=len(persistent)
                self.rows={r['key']:hydrated_listing(r,self.cache.get(r['address']) or cached_saved_position(web.address_points,r['address'])) for r in stored}
                self.complete_db=True;self.phase='positions';self.total_addresses=len(addresses);self.placed=sum(has_point(r) for r in self.rows.values())
                for row in stored:by_address[row['address']].append(row['key'])
            newly_geocoded={}
            def positioned(address,point,error):
                with self.lock:
                    self.done_addresses+=1
                    for key in by_address[address]:
                        old=self.rows[key];new=hydrated_listing(old,point);self.rows[key]=new
                        self.placed+=int(has_point(new))-int(has_point(old))
                    self.cache[address]=point
                    if point and address not in persistent:newly_geocoded[address]=point;self.geocoded_addresses+=1
            hydrate_saved_units(stored,self.bounds,self.cache,web,positioned)
            if newly_geocoded:
                try:
                    self.db.save_address_points(newly_geocoded)
                    with self.lock:self.diagnostic['persistent_address_cache_saved']=len(newly_geocoded)
                except AppError as exc:
                    with self.lock:self.diagnostic['persistent_address_cache_save_error']=str(exc)
            with self.lock:self.phase='complete'
        except SearchCancelled:
            with self.lock:self.phase='stopped'
        except Exception as exc:
            with self.lock:self.error=str(exc) if isinstance(exc,AppError) else '読み込みエラー（'+type(exc).__name__+'）';self.phase='failed'
        finally:
            with self.lock:
                self.diagnostic.update(geocoded_total=self.placed,returned=len(self.rows),database_complete=self.complete_db,
                                       persistent_address_cache_hits=self.persistent_cache_hits,newly_geocoded_addresses=self.geocoded_addresses)
                self.ended=time.monotonic();self.finished=True


@st.cache_resource
def saved_load_registry():return {},threading.RLock()


def active_saved_load():
    jobs,lock=saved_load_registry();token=st.session_state.get('saved_load_token');key=st.session_state.get('saved_load_key')
    with lock:
        job=jobs.get(key)
        current_key=st.session_state.get('current_storage_key',key)
        return job if job and job.token==token and current_key==key else None


def start_saved_load(bounds,cache=None,db=None):
    db=db or Database();key=manual_registry_key(db);jobs,lock=saved_load_registry()
    with lock:
        current=jobs.get(key)
        if current and not current.snapshot()['finished']:job=current
        else:
            job=SavedLoadJob(db,bounds,cache);jobs[key]=job;job.thread.start()
    st.session_state.saved_load_token=job.token;st.session_state.saved_load_key=key
    st.session_state.pop('saved_load_export',None)
    st.session_state.pop('saved_load_applied_token',None)
    return job


def manual_search_running():
    jobs,lock=job_registry();token=st.session_state.get('new_job_token');server_key=st.session_state.get('manual_server_key')
    with lock:
        job=jobs.get(token)
        if job and not job.finished:return True
        return any(getattr(j,'server_key',None)==server_key and not j.finished for j in jobs.values()) if server_key else False


def apply_saved_load(job):
    if active_saved_load() is not job or manual_search_running():return False
    rows,cache,diag=job.export();state=st.session_state
    state.address_point_cache=cache;state.new_units=rows;state.new_saved_keys=[r['key'] for r in rows]
    state.new_search=None;state.new_map_loaded=True;state.new_load_diagnostic=diag;state.pop('new_job_token',None)
    state.new_notice=f"{len(rows)}件を表示しました。地図配置 {sum(has_point(r) for r in rows)}件・配置未確認 {sum(not has_point(r) for r in rows)}件。"
    return True


@st.fragment(run_every='2s')
def saved_load_progress():
    job=active_saved_load()
    if not job:return
    snap=job.snapshot();searching=manual_search_running()
    applied=st.session_state.get('saved_load_applied_token')==job.token

    # A completed saved-data load must transition to the map automatically.  In v61
    # the worker could already be complete (e.g. 735/735 addresses) while the fragment
    # continued to show an ever-increasing elapsed timer and waited for another button.
    if snap['finished'] and snap['phase']=='complete' and not applied and not searching:
        if apply_saved_load(job):
            st.session_state.saved_load_applied_token=job.token
            st.rerun()

    if snap['phase']=='database':
        st.info(f"保存データ読み込み中｜{snap['rows']}件・{snap['pages']}ページ｜経過 {snap['elapsed']}秒")
    elif snap['finished'] and snap['phase']=='complete':
        st.success(f"読み込み完了｜保存データ {snap['rows']}件｜地図準備 {snap['done_addresses']}/{snap['addresses']}住所｜配置 {snap['placed']}件｜住所キャッシュ {snap['persistent_cache_hits']}件｜所要 {snap['elapsed']}秒")
    elif snap['phase']=='failed':
        st.error(f"読み込み停止｜保存データ {snap['rows']}件｜地図準備 {snap['done_addresses']}/{snap['addresses']}住所｜配置 {snap['placed']}件｜所要 {snap['elapsed']}秒")
    elif snap['phase']=='stopped':
        st.info(f"読み込みを中止しました｜保存データ {snap['rows']}件｜地図準備 {snap['done_addresses']}/{snap['addresses']}住所｜配置 {snap['placed']}件｜所要 {snap['elapsed']}秒")
    else:
        st.info(f"保存データ {snap['rows']}件｜地図準備 {snap['done_addresses']}/{snap['addresses']}住所｜配置 {snap['placed']}件｜住所キャッシュ {snap['persistent_cache_hits']}件｜経過 {snap['elapsed']}秒")
    if snap['error']:st.error(snap['error'])
    if snap['phase']=='stopped':st.caption('中止しました。読み込み済みの物件は保持しています。')
    if searching:st.caption('物件検索が終わると読み込み済みデータを地図へ反映できます。')
    elif snap['finished'] and snap['phase']=='complete':st.caption('読み込み完了後、自動的に地図へ反映します。')
    else:st.caption('地図操作は継続できます。読み込み途中でも下のボタンで現在までの物件を地図へ反映できます。')
    if not applied and st.button('読み込んだ物件を地図へ反映',key='apply_saved_load',disabled=not snap['rows'] or searching):
        if apply_saved_load(job):
            if snap['finished'] and snap['phase']=='complete':st.session_state.saved_load_applied_token=job.token
            st.rerun()
    if not snap['finished'] and st.button('読み込み・地図準備を中止（読み込み済みを保持）',key='stop_saved_load',disabled=snap['stopping']):job.request_stop()
    if st.button('読み込み済み全物件のCSVを準備',key='prepare_saved_load_csv',disabled=not snap['rows']):
        rows,_,_=job.export();st.session_state.saved_load_export={'token':job.token,'csv':csv_bytes(rows),'count':len(rows)}
    export=st.session_state.get('saved_load_export')
    if export and export['token']==job.token:
        st.download_button(f"読み込み済み {export['count']}件のCSV",export['csv'],'sumai_saved_units.csv','text/csv',key='download_saved_load_csv',on_click='ignore')


def capture_viewport():
    data=st.session_state.get('new_map',{})
    bounds=viewport_bounds(data)
    if bounds:
        st.session_state.new_bounds=bounds
        st.session_state.new_view_center=bounds_center(bounds)
        try: st.session_state.new_view_zoom=int(data.get('zoom') or 15)
        except (TypeError,ValueError): pass
    else:
        st.session_state.new_bounds=None


def remember_search():
    state=st.session_state
    if automatic_busy():
        state.new_search={'status':'failed','summary':{'issues':['自動収集を停止してから手動検索を開始してください。'],'saved':0,'confirmed':0}}
        return
    if state.get('manual_search_scope','地図の表示範囲')=='区・町名を選択':
        code=state.get('manual_ward','13116')
        state.new_request={'bounds':[34,138,37,141],'providers':['SUUMO'],'ward_code':code,
                           'town_codes':list(state.get('manual_selected_'+code,[])), 'mode':'ward_search',
                           'display':{'layouts':'all','monthly_limit':None,'aggregation':False}}
        state.new_map_loaded=True
        return
    selected=list(state.get('new_providers',[]))
    providers=list(dict.fromkeys(canonical_provider(p) for p in selected))
    bounds=state.get('new_bounds')
    state.new_preferences={'new_providers':selected}
    state.new_map_loaded=True
    if not providers or not bounds:
        state.new_search={'status':'failed','summary':{'issues':['地図の表示範囲と取得元を確認してください。'],'saved':0,'confirmed':0}}
        return
    state.new_request=dict(bounds=list(bounds),providers=providers,display={'layouts':'all','monthly_limit':None,'aggregation':False})



class WorkerState:
    def __init__(self):
        self.new_units=[];self.new_search=None;self.new_saved_keys=[];self.new_regions=[]

class SearchJob:
    def __init__(self,db,conditions):
        self.lock=threading.RLock();self.state=WorkerState();self.cancel_event=threading.Event();self.state.cancel_event=self.cancel_event;self.audit=AuditLog(conditions,(getattr(db,'key',''),));self.state.audit=self.audit;self.started_at=utc_now();self.updated_at=self.started_at;self.progress=0.;self.message='バックグラウンド検索を開始しています';self.text='';self.finished=False
        self.thread=threading.Thread(target=self.run,args=(db,conditions),daemon=True,name='housing-search')
    def run(self,db,conditions):
        job=self
        class Display:
            def progress(self,value,text=''):
                with job.lock: job.progress=value
            def info(self,value):
                with job.lock: job.message=value;job.updated_at=utc_now()
            def code(self,value,language=None):
                with job.lock: job.text=value
        screen={k:Display() for k in ('bar','status','log')}
        try: search_all(db,conditions,screen,self.state)
        except Exception as exc:
            self.audit.add('search',diagnosis_code(str(exc)),'ERROR',{'message':str(exc) if isinstance(exc,AppError) else type(exc).__name__,'traceback':traceback.format_exc()})
            self.state.new_search={'status':'failed','conditions':conditions,'summary':{'issues':[str(exc) if isinstance(exc,AppError) else '検索処理エラー（'+type(exc).__name__+'）'],'saved':len(self.state.new_saved_keys),'confirmed':len(self.state.new_units)}}
        finally:
            with self.lock: self.finished=True
    def request_stop(self):
        self.cancel_event.set()
        with self.lock:self.message='中止要求を受け付けました。新しい取得を止め、通信終了後に取得済みデータを保存・確認して終了します。'
    def snapshot(self):
        with self.lock:
            return dict(progress=self.progress,message=self.message,log=self.text,finished=self.finished,stopping=getattr(self,'cancel_event',threading.Event()).is_set(),
                        updated_at=getattr(self,'updated_at',None),last_saved_at=getattr(self.state,'new_last_saved_at',None),units=list(self.state.new_units),saved=list(self.state.new_saved_keys),result=self.state.new_search)

class PositionRepairJob(SearchJob):
    def __init__(self,db,rows,bounds,saved):
        self.lock=threading.RLock();self.cancel_event=threading.Event();self.state=WorkerState();self.state.cancel_event=self.cancel_event;self.state.new_units=[dict(r) for r in rows];self.state.new_saved_keys=list(saved)
        self.conditions={'bounds':list(bounds),'mode':'published_map_position_repair','providers':list({r['provider'] for r in rows})}
        self.audit=AuditLog(self.conditions,(getattr(db,'key',''),));self.state.audit=self.audit
        self.progress=0.;self.message='掲載物件の地図から位置と住所を再取得しています';self.text='';self.finished=False;self.updated=0
        self.thread=threading.Thread(target=self.run,args=(db,self.conditions),daemon=True,name='housing-map-position')
    def run(self,db,conditions):
        rows=[dict(r) for r in self.state.new_units];saved=set(self.state.new_saved_keys);issues=[];updated=0;attempted=0
        history={'id':self.audit.search_id,'status':'started','conditions':conditions,'summary':{},'started_at':utc_now(),'finished_at':None}
        web=PublicWeb();web.audit=self.audit;web.cancel_event=self.cancel_event
        if hasattr(web,'configure'):web.configure(getattr(db,'web_config',{}))
        try:
            try:db.save_search(history)
            except Exception as exc:issues.append('位置確認の開始履歴を保存できません');self.audit.add('database','save','WARNING',{'stage':'position_start_history','exception_type':type(exc).__name__})
            try:munis=municipalities(web)
            except Exception as exc:munis={};self.audit.add('location','reverse_address_pending','WARNING',{'reason':'市区町村コード表を取得できない','exception_type':type(exc).__name__})
            targets=[i for i,r in enumerate(rows) if needs_position_repair(r)]
            for done,i in enumerate(targets,1):
                web.check_cancel()
                old=rows[i];attempted+=1;point=None
                with self.lock:self.message=f'物件地図の位置確認 {done}/{len(targets)}件｜改善 {updated}件'
                self.audit.add('location','position_repair_start','INFO',{'listing_url':old['listing_url'],'old_point':[old.get('latitude'),old.get('longitude')],'old_precision':old.get('coordinate_precision')})
                try:
                    if not web.permitted(old['listing_url']):raise AppError('掲載物件の自動取得ルールで取得できません。')
                    reply=web.fetch(old['listing_url']);soup=BeautifulSoup(reply.text,'html.parser')
                    address=labeled(soup,('所在地','住所','物件所在地')) or old.get('address','')
                    point=locate(web,soup,reply.text,address,conditions['bounds'],munis,source_url=old['listing_url'])
                    if point and point.get('coordinate_precision')=='listing_map':
                        rows[i]=merge_listing(old,dict(old,**point,address=address))
                        try:
                            saved.update(db.save_units([rows[i]]));updated+=1
                            self.audit.add('location','position_repaired','INFO',{'listing_url':old['listing_url'],'new_position':point,'saved':True})
                        except Exception as exc:saved.discard(old['key']);issues.append('位置更新の保存を確認できません');self.audit.add('database','save','ERROR',{'listing_url':old['listing_url'],'exception_type':type(exc).__name__})
                        self.audit.add('location','position_extracted','INFO',{'listing_url':old['listing_url'],'new_position':point,'saved':old['key'] in saved})
                    else:self.audit.add('location','position_pending','WARNING',{'listing_url':old['listing_url'],'reason':'掲載地図の座標または画像の基準位置を取得できない','retained':True})
                except Exception as exc:
                    issues.append(str(exc) if isinstance(exc,AppError) else '掲載地図の読取失敗')
                    self.audit.add('location','position_pending','WARNING',{'listing_url':old['listing_url'],'exception_type':type(exc).__name__,'message':str(exc) if isinstance(exc,AppError) else '掲載地図の読取失敗','retained':True})
                with self.lock:
                    self.state.new_units=list(rows);self.state.new_saved_keys=list(saved);self.progress=done/max(1,len(targets));self.updated=updated
                    self.text=f'確認 {done}/{len(targets)}件｜位置改善 {updated}件｜全募集 {len(rows)}件を保持'
                self.audit.persist(db)
            history.update(status='partial' if issues or any(needs_position_repair(r) for r in rows) else 'completed',finished_at=utc_now(),
                summary={'confirmed':len(rows),'saved':len(saved),'position_attempted':attempted,'position_updated':updated,
                    'position_pending':sum(needs_position_repair(r) for r in rows),'issues':list(dict.fromkeys(issues)),'individual_positions':len({(r.get('latitude'),r.get('longitude')) for r in rows if has_point(r)})})
            self.audit.add('location','position_repair_finish','INFO',history['summary']);self.audit.persist(db,force=True)
            db.save_search(history)
        except SearchCancelled:
            history.update(status='cancelled',finished_at=utc_now(),summary={'confirmed':len(rows),'saved':len(saved),'position_attempted':attempted,'position_updated':updated,'issues':issues})
            try:db.save_search(history)
            except Exception:pass
        except Exception as exc:
            history.update(status='partial',finished_at=utc_now(),summary={**history.get('summary',{}),'confirmed':len(rows),'saved':len(saved),'issues':[str(exc) if isinstance(exc,AppError) else '位置更新の保存・ログ処理に失敗しました']})
            self.audit.add('location','position_pending','ERROR',{'exception_type':type(exc).__name__,'retained':len(rows)})
        finally:
            with self.lock:self.state.new_search=history;self.finished=True


def needs_position_repair(row):
    return not has_point(row) or row.get('coordinate_precision')=='town' or str(row.get('location_method') or '').startswith('住所検索')


def start_position_repair(rows,bounds,db=None):
    if not rows or not bounds or not any(needs_position_repair(r) for r in rows):return
    jobs,lock=job_registry();state=st.session_state
    token=state.setdefault('new_job_token',hashlib.sha256(os.urandom(32)).hexdigest())
    with lock:
        old=jobs.get(token)
        if old and not old.snapshot()['finished']:return
        db=db or Database();db.web_config=rental_network_settings()
        job=PositionRepairJob(db,rows,bounds,state.get('new_saved_keys',[]));jobs[token]=job;job.created=time.monotonic();job.thread.start()
    state.new_search=None


@st.cache_resource
def job_registry():
    return {},threading.RLock()

def active_job():
    jobs,lock=job_registry()
    with lock:
        old=jobs.get(st.session_state.get('new_job_token'))
        if old and not old.snapshot()['finished']:return old
        server_key=st.session_state.get('manual_server_key')
        if server_key:
            for token,job in jobs.items():
                if getattr(job,'server_key',None)==server_key and not job.snapshot()['finished']:
                    st.session_state.new_job_token=token;return job
        return old

def manual_registry_key(db):
    return hashlib.sha256((str(getattr(db,'url',''))+'|'+str(db.namespace)).encode()).hexdigest()

def restore_manual_search():
    if st.session_state.get('manual_job_checked'):return
    st.session_state.manual_job_checked=True
    if active_job():return
    try:
        key=manual_registry_key(Database());st.session_state.manual_server_key=key
    except Exception:return
    jobs,lock=job_registry()
    with lock:
        for token,job in jobs.items():
            if getattr(job,'server_key',None)==key and not job.snapshot()['finished']:
                st.session_state.new_job_token=token;st.session_state.new_map_loaded=True
                snap=job.snapshot();st.session_state.new_units=snap['units'];st.session_state.new_saved_keys=snap['saved']
                return

def start_search(conditions):
    if automatic_busy():raise AppError('自動収集を停止してから手動検索を開始してください。')
    jobs,lock=job_registry()
    token=st.session_state.setdefault('new_job_token',hashlib.sha256(os.urandom(32)).hexdigest())
    with lock:
        old=jobs.get(token)
        if old and not old.snapshot()['finished']: return
        # Start with an empty map and reflect this search as rows arrive.
        st.session_state.new_map_loaded=True
        st.session_state.new_units=[]
        st.session_state.new_saved_keys=[]
        # Secrets are read on the Streamlit thread, before launching a pure Python worker.
        db=Database();db.web_config=rental_network_settings()
        server_key=manual_registry_key(db);st.session_state.manual_server_key=server_key
        for running_token,running in jobs.items():
            if getattr(running,'server_key',None)==server_key and not running.snapshot()['finished']:
                st.session_state.new_job_token=running_token;return
        job=SearchJob(db,conditions);job.server_key=server_key;jobs[token]=job
        # Release finished jobs from other sessions after two hours.
        for key,value in list(jobs.items()):
            if key!=token and value.finished and time.monotonic()-getattr(value,'created',time.monotonic())>7200: jobs.pop(key,None)
        job.created=time.monotonic();job.thread.start()
    st.session_state.new_search=None

@st.fragment(run_every='2s')
def background_progress():
    """Render live worker progress and buttons inside the fragment-owned container."""
    job=active_job()
    if job is None or job.snapshot()['finished']:
        controller=get_automatic_collection()
        auto=controller.snapshot() if controller else None
        if auto and auto['running']:
            st.progress(min(.99,max(0.,auto['progress'])),text='サーバー側で自動収集しています')
            st.caption(auto['message'])
            current=auto.get('current',{})
            if current:st.caption('処理更新：'+acquisition_time_jst(current.get('updated_at'))+'｜最終物件保存：'+acquisition_time_jst(current.get('last_saved_at')))
            if st.button('自動収集を中止（取得済みデータを保存）',key='auto_stop_map',disabled=auto['stopping']):
                try:controller.request_stop()
                except AppError as exc:st.error('中止は要求しましたが、停止設定の保存に失敗しました：'+str(exc))
            return
        if job is None:return
    snap=job.snapshot()
    if not snap['finished'] or st.session_state.get('finished_snapshot_applied')!=job.audit.search_id:
        st.session_state.new_units=snap['units'];st.session_state.new_saved_keys=snap['saved']
        if snap['finished']:st.session_state.finished_snapshot_applied=job.audit.search_id
    if snap['finished']:
        st.session_state.new_search=snap['result']
    def refresh_map():
        st.session_state.new_units=snap['units'];st.session_state.new_saved_keys=snap['saved']
        st.rerun()
    def render():
        st.caption('処理更新：'+acquisition_time_jst(snap.get('updated_at'))+'｜最終物件保存：'+acquisition_time_jst(snap.get('last_saved_at')))
        if snap['finished']:
            result=snap.get('result') or {};summary=result.get('summary',{})
            st.info(f"検索終了｜状態 {result.get('status','不明')}｜取得 {len(snap['units'])}件｜保存確認 {len(snap['saved'])}件")
            if summary.get('unsaved'):st.error('未保存 '+str(summary['unsaved'])+'件。保存に失敗したデータはサーバーのメモリに保持しています。作業ログを確認してください。')
            if summary.get('acquisition_ids_pending'):st.error('取得済みIDの保存未確認 '+str(summary['acquisition_ids_pending'])+'件。次回は再取得対象になります。')
            if st.button('取得済みデータを地図へ反映',key='refresh_finished_map'):refresh_map()
            return
        st.progress(min(.99,snap['progress']),text='バックグラウンドで地図位置・住所を確認しています' if isinstance(job,PositionRepairJob) else 'バックグラウンドで検索・保存しています')
        st.caption('中断処理中｜'+snap['message'] if snap.get('stopping') else snap['message'])
        st.caption('画面を閉じたり他のアプリへ移っても、サーバー稼働中は取得・保存を継続します。')
        st.caption(f"地図操作優先：自動再描画を停止中｜取得済み {len(snap['units'])}件。検索・保存は継続します。")
        if st.button('取得済みデータを地図へ反映',key='refresh_live_map'):refresh_map()
        if st.button('取得を中止（取得済みデータを保存）',key='stop_background_job',disabled=bool(snap.get('stopping'))):
            job.request_stop()
    render()


@st.fragment(run_every='10s')
def background_status():
    """Render diagnostics below the search controls; progress itself is shown under the map."""
    job=active_job()
    if job is None:return
    snap=job.snapshot()
    st.caption(f"作業ログ {job.audit.count}イベント｜Supabase保存確認 {job.audit.persisted_events}イベント")
    # Keep polling cheap: build a complete export only when explicitly requested.
    if st.button('作業ログを準備・更新（検索中も利用できます）',key='current_log_prepare'):
        try:
            with st.spinner('現在までの作業ログを準備しています'):
                events=job.audit.records()
                st.session_state.diagnostic_export_cache={'id':job.audit.search_id,'events':events,'count':len(events),'prepared_at':utc_now()}
        except Exception as exc:
            st.error('作業ログを準備できませんでした：'+type(exc).__name__)
    audit_cache=st.session_state.get('diagnostic_export_cache')
    if audit_cache and audit_cache['id']==job.audit.search_id:
        diagnostic_downloads(audit_cache['events'])
        st.caption(f"ダウンロード対象：{audit_cache['count']}イベント｜準備日時：{acquisition_time_jst(audit_cache['prepared_at'])}｜最新分を含めるには「準備・更新」を押してください。")
    else:
        st.caption('「作業ログを準備・更新」を押すとJSON・TXTのダウンロードボタンが表示されます。')
    if job.audit.storage_error:st.error('作業ログのSupabase保存を確認できません。準備・更新ボタンから、このサーバーに残るログをダウンロードできます。'+job.audit.storage_error)

    if not snap['finished']:
        if snap['log']:st.code(snap['log'],language=None)
        st.caption('画面を操作しても検索と保存は継続します。サーバーの休止・再起動では実行が終了します。')

def capture_map_fragment():
    had_bounds=bool(st.session_state.get('new_bounds'))
    capture_viewport()
    if not had_bounds and st.session_state.get('new_bounds'):
        st.session_state['map_initial_bounds_ready']=True

@st.fragment
def interactive_rental_map(pins,cells,facilities):
    state=st.session_state
    st_folium(rental_map([],DEFAULT_CENTER,1500,[],[]),key='new_map',height=480,use_container_width=True,
        returned_objects=['bounds','zoom'],center=state.get('new_view_center',DEFAULT_CENTER),
        zoom=state.get('new_view_zoom',15),feature_group_to_add=rental_features(pins,cells,facilities),on_change=capture_map_fragment)
    if state.pop('map_initial_bounds_ready',False):
        st.rerun()


AUTO_COLLECTION_ID='automatic.collection.settings'

class AutomaticCollection:
    """One low-concurrency collection loop per saved namespace; no Streamlit UI in its thread."""
    def __init__(self,db,settings,summary=None):
        validate_automatic_settings(settings);validate_automatic_summary(summary or {})
        self.db=db;self.settings=dict(settings);self.settings['interval_minutes']=0;self.summary=dict(summary or {});self.summary.pop('next_run_at',None)
        self.lock=threading.RLock();self.io_lock=threading.Lock();self.stop_event=threading.Event()
        self.request_starts={};self.shared_web=PublicWeb();self.last_job=None;self.current=None;self.message='自動収集を準備しています';self.progress=0.;self.error=''
        self.thread=threading.Thread(target=self.run,daemon=True,name='housing-automatic-collection')
    def persist(self):
        with self.io_lock:
            with self.lock:
                record={'id':AUTO_COLLECTION_ID,'status':'automatic_collection_settings','started_at':self.settings.get('created_at',utc_now()),
                        'finished_at':None,'conditions':json.loads(json.dumps(self.settings)), 'summary':json.loads(json.dumps(self.summary))}
            self.db.save_search(record)
    def snapshot(self):
        with self.lock:
            job=self.current
            result=dict(settings=dict(self.settings),summary=dict(self.summary),message=self.message,error=self.error,
                        running=self.thread.is_alive(),stopping=self.stop_event.is_set(),progress=self.progress)
        if job:
            snap=job.snapshot();result.update(message=snap['message'],progress=snap['progress'],current=snap)
        return result
    def request_stop(self):
        with self.lock:
            self.stop_event.set()
            self.settings['enabled']=False;self.message='停止要求を受け付けました。取得済みデータを保存して終了します。'
            job=self.current
        if job:job.request_stop()
        self.persist()
    def run(self):
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    if self.stop_event.is_set():break
                    conditions={'bounds':[34,138,37,141],'ward_code':self.settings['ward_code'],'providers':list(self.settings['providers']),
                                'mode':'automatic_collection','auto_task_index':int(self.summary.get('next_task_index',0)),
                                'town_codes':list(self.settings.get('town_codes',[]))}
                    for key in ('auto_regions','auto_munis'):
                        if self.settings.get(key):conditions[key]=self.settings[key]
                    self.summary['last_started_at']=utc_now();self.summary['current_status']='running'
                    job=SearchJob(self.db,conditions)
                    if hasattr(job,'state'):job.state.request_starts=self.request_starts;job.state.shared_web=self.shared_web
                    self.current=job
                self.persist()
                # Run synchronously on this daemon. The normal search persists each acquired batch.
                job.run(self.db,conditions)
                snap=job.snapshot();result=snap.get('result') or {};status=result.get('status','failed');incomplete=bool(result.get('summary',{}).get('incomplete_tasks'))
                with self.lock:
                    self.last_job=job;self.current=None;self.progress=1.;self.summary['last_finished_at']=utc_now();self.summary['last_status']=status
                    self.summary['current_status']='stopped';self.summary['last_task']=conditions.get('auto_task_label','地域判定')
                    self.summary['last_summary']=result.get('summary',{})
                    self.summary['saved_observations']=int(self.summary.get('saved_observations',0))+len(snap['saved'])
                    stats=result.get('summary',{})
                    for name,source in (('new_saved_observations','new_saved'),('updated_saved_observations','updated_saved'),('failed_save_observations','unsaved'),('processed_observations','processed'),('detail_observations','detail'),('skipped_observations','already_acquired'),('rejected_observations','rejected')):
                        self.summary[name]=int(self.summary.get(name,0))+int(stats.get(source,0))
                    self.summary['statistics_started_at']=self.summary.get('statistics_started_at') or self.summary['last_started_at']
                    self.summary['collection_seconds']=float(self.summary.get('collection_seconds',0))+float(stats.get('elapsed',0))
                    self.summary['confirmed_observations']=int(self.summary.get('confirmed_observations',0))+len(snap['units'])
                    if status in ('completed','partial') and not incomplete:
                        total=max(1,int(conditions.get('auto_total_tasks',1)))
                        self.summary['next_task_index']=(int(conditions.get('auto_task_index',0))+1)%total
                        self.summary['total_tasks']=total
                        self.summary['completed_runs']=int(self.summary.get('completed_runs',0))+1
                        for key in ('auto_regions','auto_munis'):
                            if conditions.get(key):self.settings[key]=conditions[key]
                    self.summary.pop('next_run_at',None)
                    if status=='failed' or incomplete:
                        self.settings['enabled']=False;self.stop_event.set()
                        self.error='今回の町の全ページ確認を完了できませんでした。原因を確認して同じ町から再開してください。'
                    self.message='取得に失敗したため自動収集を中止しました。' if status=='failed' or incomplete else '取得・保存処理を中止しました。' if status=='cancelled' else '今回の収集を保存しました。待機せず次の町へ進みます。'
                self.persist()
        except Exception as exc:
            with self.lock:
                self.error=str(exc) if isinstance(exc,AppError) else '自動収集エラー（'+type(exc).__name__+'）'
                self.message='自動収集が停止しました。設定画面から再開してください。'
                self.summary['last_status']='failed';self.settings['enabled']=False
            try:self.persist()
            except Exception:pass
        finally:
            with self.lock:self.current=None

@st.cache_resource
def automatic_registry():
    return {},threading.RLock()

def automatic_registry_key(db):
    return hashlib.sha256((db.url+'|'+db.namespace).encode()).hexdigest()

def get_automatic_collection():
    controller=st.session_state.get('automatic_collection_controller')
    if controller:
        registry,lock=automatic_registry()
        with lock:controller=registry.get(automatic_registry_key(controller.db),controller)
        st.session_state.automatic_collection_controller=controller
    return controller

def automatic_busy():
    controller=get_automatic_collection()
    return bool(controller and controller.snapshot()['running'])

def restore_automatic_collection():
    # One lookup when a browser session starts. It never runs on map pan or progress polls.
    if st.session_state.get('automatic_settings_checked'):return
    st.session_state.automatic_settings_checked=True
    try:
        db=Database();db.web_config=rental_network_settings();registry,lock=automatic_registry();key=automatic_registry_key(db)
        with lock:
            controller=registry.get(key)
            if controller and controller.thread.is_alive():
                st.session_state.automatic_collection_controller=controller;return
            rows=db.call('GET',SEARCH_TABLE,{'namespace':'eq.'+db.namespace,'id':'eq.'+AUTO_COLLECTION_ID,'select':'conditions,summary','limit':1})
            if not isinstance(rows,list):raise AppError('自動収集設定を読み取れません。')
            if not rows:return
            settings=rows[0].get('conditions') or {};summary=rows[0].get('summary') or {}
            validate_automatic_settings(settings);validate_automatic_summary(summary)
            st.session_state.automatic_saved_settings=settings;st.session_state.automatic_saved_summary=summary
            if not settings.get('enabled'):return
            controller=AutomaticCollection(db,settings,summary);registry[key]=controller
            st.session_state.automatic_collection_controller=controller;controller.thread.start()
    except Exception as exc:st.session_state.automatic_settings_error=str(exc) if isinstance(exc,AppError) else '自動収集設定の復元に失敗しました（'+type(exc).__name__+'）'

def validate_automatic_settings(settings):
    if not isinstance(settings,dict):raise AppError('自動収集設定の形式が不正です。')
    if settings.get('ward_code') not in TOKYO_WARDS:raise AppError('東京23区から対象区を選択してください。')
    codes=settings.get('town_codes',[])
    if not isinstance(codes,list) or any(not isinstance(c,str) or not re.fullmatch(re.escape(settings['ward_code'])+r'\d{3}',c) for c in codes):raise AppError('対象町名の設定が不正です。')
    if settings.get('providers')!=['SUUMO']:raise AppError('自動収集はSUUMOを指定してください。')

def validate_automatic_summary(summary):
    if not isinstance(summary,dict):raise AppError('自動収集の進捗形式が不正です。')
    for key in ('next_task_index','total_tasks','completed_runs','saved_observations','confirmed_observations'):
        try:
            if key in summary and int(summary[key])<0:raise ValueError()
        except (ValueError,TypeError):raise AppError('自動収集の進捗が不正です：'+key) from None
    if not isinstance(summary.get('last_summary',{}),dict):raise AppError('自動収集の前回結果が不正です。')

def start_automatic_collection(ward_code,interval_minutes=0,town_codes=None):
    settings={'enabled':True,'ward_code':ward_code,'providers':['SUUMO'],'interval_minutes':0,'created_at':utc_now(),'town_codes':sorted(set(town_codes or []))}
    validate_automatic_settings(settings)
    db=Database();db.web_config=rental_network_settings();registry,lock=automatic_registry();key=automatic_registry_key(db)
    with lock:
        old=registry.get(key)
        if old and old.thread.is_alive():raise AppError('自動収集が稼働中です。停止してから範囲を変更してください。')
        old_snapshot=old.snapshot() if old else {}
        prior=old_snapshot.get('settings',st.session_state.get('automatic_saved_settings',{}))
        previous_summary=old_snapshot.get('summary',st.session_state.get('automatic_saved_summary',{}))
        same_scope=prior.get('ward_code')==settings['ward_code'] and sorted(prior.get('town_codes',[]))==settings['town_codes']
        summary=previous_summary if same_scope else {}
        for item in ('auto_regions','auto_munis'):
            if same_scope and prior.get(item):settings[item]=prior[item]
        controller=AutomaticCollection(db,settings,summary);controller.persist()
        registry[key]=controller;st.session_state.automatic_collection_controller=controller
        st.session_state.automatic_settings_error='';controller.thread.start()
    return controller

@st.fragment(run_every='5s')
def automatic_collection_panel():
    st.subheader('アプリ内の自動収集（SUUMO）')
    st.caption('画面を閉じてもサーバー稼働中は収集します。休止・再起動では止まり、次にアプリを開くと保存した設定・町の順番から再開します。')
    controller=get_automatic_collection();snap=controller.snapshot() if controller else None
    busy=bool(snap and snap['running'])
    bounds=st.session_state.get('new_bounds')
    saved=snap['settings'] if snap else st.session_state.get('automatic_saved_settings',{})
    ward_code=st.selectbox('自動収集する区',list(TOKYO_WARDS),index=list(TOKYO_WARDS).index(saved.get('ward_code','13116')),format_func=lambda code:TOKYO_WARDS[code],key='automatic_ward')
    town_codes=ward_town_selector(ward_code,'automatic',saved.get('town_codes',[]),disabled=busy)
    st.caption('町ごとの待機時間はありません。件数・ページ数の取得上限は設けず、各町を最後のページまで確認してから次の町へ進みます。')
    st.caption('選択した町を順番に検索します。町名未選択なら区の全町が対象です。築15年以内・マンション・1LDK/2K/2DK・2LDK/3K/3DKが対象です。過去3か月以内の取得済みIDは詳細取得前にスキップします。設定は停止後に変更できます。')
    manual=active_job();manual_busy=bool(manual and not manual.snapshot()['finished'])
    if st.button('選択した区で自動収集を開始・再開',key='automatic_start',disabled=busy or manual_busy):
        try:
            controller=start_automatic_collection(ward_code,town_codes=town_codes);snap=controller.snapshot();busy=True
        except AppError as exc:st.error(str(exc))
    if st.button('自動収集を中止（取得済みデータを保存）',key='automatic_stop',disabled=not busy or bool(snap and snap['stopping'])):
        try:
            controller.request_stop();snap=controller.snapshot()
            st.session_state.automatic_saved_settings=snap['settings'];st.session_state.automatic_saved_summary=snap['summary']
            st.success('停止を要求しました。保存済みデータは保持します。')
        except AppError as exc:st.error('実行の停止は要求しましたが、停止設定の保存に失敗しました：'+str(exc))
    if snap:
        summary=snap['summary'];st.write(snap['message']);st.progress(min(1.,max(0.,snap['progress'])))
        st.caption('対象区：'+TOKYO_WARDS[snap['settings']['ward_code']]+'｜町名指定：'+str(len(snap['settings'].get('town_codes',[])))+'町（0なら区全体）')
        st.caption(f"完了した収集 {summary.get('completed_runs',0)}回｜延べ保存確認 {summary.get('saved_observations',0)}件｜次の町 {int(summary.get('next_task_index',0))+1}/{summary.get('total_tasks','未判定')}")
        if summary.get('statistics_started_at'):
            seconds=max(1,float(summary.get('collection_seconds',0)));processed=int(summary.get('processed_observations',0));rate=processed*3600/seconds
            st.caption(f"v58集計：新規保存 {summary.get('new_saved_observations',0)}件｜更新 {summary.get('updated_saved_observations',0)}件｜未保存 {summary.get('failed_save_observations',0)}件｜詳細確認 {summary.get('detail_observations',0)}件｜取得済みスキップ {summary.get('skipped_observations',0)}件｜除外 {summary.get('rejected_observations',0)}件")
            st.caption(f"集計開始 {acquisition_time_jst(summary['statistics_started_at'])}｜処理速度 {rate:.0f}件/時（取得済みスキップを含む）｜8時間換算 {rate*8:.0f}件・達成保証ではありません")
        st.caption('最終終了：'+acquisition_time_jst(summary.get('last_finished_at')))
        st.caption('前回の取得済みIDスキップ：'+str(summary.get('last_summary',{}).get('already_acquired',0))+'件')
        if summary.get('last_task'):st.caption('前回の町：'+summary['last_task']+'｜結果：'+str(summary.get('last_status','')))
        for issue in summary.get('last_summary',{}).get('issues',[])[:4]:st.error(issue)
        if snap['error']:st.error(snap['error'])
    if st.session_state.get('automatic_settings_error'):st.error(st.session_state.automatic_settings_error)
    if controller and st.button('自動収集のログを準備・更新',key='automatic_log_prepare'):
        with controller.lock:log_job=controller.current or controller.last_job
        if log_job:
            events=log_job.audit.records();st.session_state.automatic_log_events=events
            st.session_state.automatic_log_search_id=log_job.audit.search_id
    if st.session_state.get('automatic_log_events'):
        diagnostic_downloads(st.session_state.automatic_log_events,'automatic')
        st.caption('ログは準備した時点の町・処理の記録です。更新するにはもう一度「準備・更新」を押してください。')
    st.caption('物件ID・取得成功日時は再取得の管理情報として保存します。詳細住所と募集データの保存に成功した物件だけが取得済みになります。')
    st.caption('蓄積した物件は「保存物件をすべて読み込む」で地図へ表示できます。収集中も地図の自動再描画は行いません。')


def main():
    st.set_page_config(page_title='住まいコンパス｜新しい住まいを探す',page_icon='🏡',layout='wide',initial_sidebar_state='collapsed')
    st.markdown(CSS,unsafe_allow_html=True)
    st.markdown(f'<div class="hero"><span class="badge">SUMAI COMPASS · {BUILD}</span><h1>次の住まいを、地図から。</h1><p>掲載物件を確認し、家賃と暮らしやすさを比べます。<br>間取りと家賃帯を、取得できた情報で比較します。</p></div>',unsafe_allow_html=True)
    state=st.session_state
    state.setdefault('new_units',[]);state.setdefault('new_search',None);state.setdefault('new_saved_keys',[])
    state.setdefault('new_facilities',[])
    state.setdefault('new_map_loaded',False);state.setdefault('address_point_cache',{})
    state.setdefault('layout_display_group','group1')
    restore_manual_search()
    restore_automatic_collection()
    request=state.pop('new_request',None)
    if request is not None:
        try: start_search(request)
        except AppError as exc: state.new_search={'status':'failed','summary':{'issues':[str(exc)],'saved':0,'confirmed':0}}
    preferences=state.get('new_preferences',{})
    try:state.current_storage_key=manual_registry_key(Database())
    except Exception:state.current_storage_key=None
    tabs=st.tabs(['住まいを探す','保存データ','通勤・周辺施設','初期設定'])
    with tabs[0]:
        st.subheader('地図を動かして、探す地域を表示してください')
        st.caption('地図に見えている四角い範囲が検索対象です。駅名の選択や取得件数の上限はありません。')
        st.caption('検索中の取得済み物件は「取得済みデータを地図へ反映」で表示できます。進捗と中断ボタンは地図のすぐ下に表示します。')
        st.caption('保存する募集データは家賃・間取り・詳細住所（推定）・データ取得日時です。地図画像や緯度経度は保存しません。')
        selected_group=state.get('layout_display_group','group1')
        if selected_group not in DISPLAY_LAYOUT_GROUPS:selected_group='group1';state.layout_display_group='group1'
        g1,g2=st.columns(2)
        if g1.button('1LDK・2K・2DK',key='layout_group1',type='primary' if selected_group=='group1' else 'secondary',use_container_width=True):
            if state.layout_display_group!='group1':state.layout_display_group='group1';st.rerun()
        if g2.button('2LDK・3K・3DK',key='layout_group2',type='primary' if selected_group=='group2' else 'secondary',use_container_width=True):
            if state.layout_display_group!='group2':state.layout_display_group='group2';st.rerun()
        selected_group=state.get('layout_display_group','group1');selected_layouts=set(DISPLAY_LAYOUT_GROUPS[selected_group])
        st.caption('表示中：'+('1LDK・2K・2DK' if selected_group=='group1' else '2LDK・3K・3DK')+'。保存データ自体は変更せず、地図・件数・一覧だけを切り替えます。')
        bounds=state.get('new_bounds')
        center=bounds_center(bounds) if bounds else state.get('new_view_center',DEFAULT_CENTER)
        radius=bounds_radius(bounds) if bounds else 1500
        map_units=[r for r in state.new_units if parsed_layout(r.get('layout')) in selected_layouts] if state.get('new_map_loaded') else []
        rows,pins,cells,display_report=display_pipeline(map_units,bounds,state.get('new_load_diagnostic'))
        job=active_job()
        if job:
            map_signature=(tuple(bounds) if bounds else None,len(map_units),tuple(display_report['stages'].values()),tuple(display_report['map'].values()))
            if state.get('diagnostic_map_signature')!=map_signature:
                state.diagnostic_map_signature=map_signature
                job.audit.add('map','render','INFO',{'bounds':bounds,'loaded_units':len(map_units),'aggregation':False,'monthly_limit':None,'renderer':'Canvas','display_stages':display_report['stages'],'display_reasons':display_report['reason_counts'],'display_map':display_report['map']})
        facilities=[f for f in state.new_facilities if bounds and in_rectangle((f['lat'],f['lng']),bounds)]
        # Leaflet clips points to its viewport. Keeping every loaded point in the layer
        # lets a pan reveal previously off-screen properties without a full app rerun.
        def legend_chip(color,label,emphasis=False):
            return (f'<span style="display:inline-flex;align-items:center;gap:5px;padding:3px 7px;'
                    f'border-radius:999px;border:{"1.5px" if emphasis else "1px"} solid #c7cec9;'
                    f'background:#ffffff;color:#203b38;font-size:12px;font-weight:{700 if emphasis else 500}">'
                    f'<i style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{color};'
                    f'border:1px solid #ffffff;box-shadow:0 0 0 1px #6f7773"></i>{label}</span>')
        legend=(
            '<div style="display:flex;flex-wrap:wrap;gap:5px 7px;margin:2px 0 5px">'+
            ''.join(legend_chip(color,label,True) for color,label in zip(COLORS[:5],RENT_BAND_LABELS[:5]))+
            '</div><div style="display:flex;flex-wrap:wrap;gap:5px 7px;margin:0 0 8px">'+
            ''.join(legend_chip(color,label,False) for color,label in zip(COLORS[5:],RENT_BAND_LABELS[5:]))+
            '</div>')
        st.markdown(legend,unsafe_allow_html=True)
        interactive_rental_map(map_units,cells,facilities)
        if st.button('現在の範囲の件数・一覧を更新',key='refresh_viewport_summary'):
            st.rerun()
        st.caption('地図を動かすと読み込み済みの物件を表示します。下の件数・一覧は「現在の範囲の件数・一覧を更新」で更新できます。')
        # Fragment owns its widget container directly, including partial reruns.
        background_progress()
        # Initial component defaults are not real viewport bounds. Only the browser callback makes the search ready.
        bounds=state.get('new_bounds')
        if bounds:
            center=bounds_center(bounds);radius=bounds_radius(bounds)
            south,west,north,east=bounds
            st.caption(f'検索する表示範囲：緯度 {south:.5f}〜{north:.5f}／経度 {west:.5f}〜{east:.5f}')
        else: st.info('地図の表示範囲を受信しています。表示後に地図を少し動かしてください。対象は東京と周辺地域です。')
        saved_provider_defaults=preferences.get('new_providers',['HOMES','SUUMO'])
        provider_defaults=[PROVIDER_DISPLAY.get(canonical_provider(x),x) for x in saved_provider_defaults]
        provider_defaults=[x for x in dict.fromkeys(provider_defaults) if x in PROVIDER_OPTIONS] or ['HOMES','SUUMO']
        if 'new_providers' in state:
            current_provider_state=[PROVIDER_DISPLAY.get(canonical_provider(x),x) for x in list(state.get('new_providers',[]))]
            current_provider_state=[x for x in dict.fromkeys(current_provider_state) if x in PROVIDER_OPTIONS]
            if list(state.get('new_providers',[]))!=current_provider_state:state.new_providers=current_provider_state
        providers=st.multiselect('物件の取得元（複数選択可）',list(PROVIDER_OPTIONS),default=provider_defaults,key='new_providers')
        st.caption('スマイティ・HOMES・SUUMO・カナリー・アットホーム・CHINTAI・Comfy・アパマンショップから、使う取得元を1つ以上選択できます。')
        scope=st.radio('検索方法',['地図の表示範囲','区・町名を選択'],horizontal=True,key='manual_search_scope')
        searching=automatic_busy() or bool(active_job() and not active_job().snapshot()['finished'])
        if scope=='区・町名を選択':
            ward_code=st.selectbox('検索する区',list(TOKYO_WARDS),index=list(TOKYO_WARDS).index(state.get('manual_selected_ward','13116')),format_func=lambda code:TOKYO_WARDS[code],key='manual_ward',disabled=searching)
            state.manual_selected_ward=ward_code
            ward_town_selector(ward_code,'manual',disabled=searching)
            st.caption('区・町名指定はSUUMOを対象に、築15年以内・マンション・1LDK/2K/2DK/2LDK/3K/3DKを検索・保存します。地図の表示範囲では絞りません。')
        st.button('表示中の地名から全件検索・保存' if scope=='地図の表示範囲' else '選択した区・町名を検索・保存',type='primary',use_container_width=True,
                  on_click=remember_search,key='new_start',disabled=searching or (scope=='地図の表示範囲' and (not bounds or not providers)))
        st.caption('検索開始時の表示範囲から検索する町名を決めます。SUUMOはその町の全丁目を対象に、範囲外でも詳細住所を推定して保存します。HOMESは区名→町域→マンション・築15年以内→①1LDK/2K/2DK→②2LDK/3K/3DK→詳細→「地図を見る」の画像認識、SUUMOは市区郡→町名→同じ2間取り群→詳細→「地図・周辺環境」の物件マーカー座標取得です。住所は実所在地未確認の推定値です。保存は家賃・間取り・詳細住所（推定）・データ取得日時です。')
        background_status()
        result=state.new_search
        if result:
            summary=result['summary'];saved=summary.get('saved',0);confirmed=summary.get('confirmed',0)
            if result.get('conditions',{}).get('mode')=='published_map_position_repair':st.info(f"位置の再確認：{summary.get('position_attempted',0)}件｜位置改善 {summary.get('position_updated',0)}件｜未確認 {summary.get('position_pending',0)}件｜全募集を保持")
            if result['status']=='completed': st.success(f'検索完了｜取得 {confirmed}件・保存確認 {saved}件')
            elif result['status']=='cancelled': st.info(f'検索を中断しました｜取得 {confirmed}件・保存確認 {saved}件を保持しています。')
            elif result['status']=='partial': st.error(f'検索終了｜取得 {confirmed}件・保存確認 {saved}件。一部の取得・保存に失敗しました。')
            else: st.error('検索を完了できませんでした。以下の原因を確認してください。')
            for issue in summary.get('issues',[])[:8]: st.write('・'+issue)
            if summary.get('elapsed') is not None: st.caption(f"所要 {summary['elapsed']}秒｜詳細確認 {summary.get('detail',0)}件｜除外 {summary.get('rejected',0)}件｜取得済みスキップ {summary.get('already_acquired',0)}件")
            if result.get('conditions',{}).get('regions'):
                with st.expander('今回検索した地名・丁目'):
                    for label in result['conditions']['regions']: st.write(label)
        st.caption('色分けは家賃＋管理費・共益費の月額です。各境界額は低い側の帯に含みます。1募集につき1点を、その募集の家賃帯で表示します。町丁目や区画の平均・中央値へまとめません。町丁目の位置しか分からない点は破線で表示します。同じ位置の募集は重なります。')
        if state.get('new_map_loaded'):display_diagnostic_downloads(display_report)
        c1,c2,c3=st.columns(3)
        c1.metric('現在の範囲の募集',len(rows))
        c2.metric('家賃帯で色付けした募集',display_report['map']['colored_points'])
        c3.metric('位置未確認の募集',display_report['map']['unconfirmed_positions'])
        if not state.get('new_map_loaded'):st.info('取得済みデータは「地図へ反映」で表示します。保存データは「保存データ」から読み込めます。')
        elif not rows:st.info('この表示範囲に配置できる保存データがありません。')
        if rows:
            with st.expander('募集を1件ずつ確認'):
                chosen=st.selectbox('確認する募集',rows,format_func=lambda r:(r.get('title') or '')+'｜'+str(r.get('layout') or '間取り未確認')+'｜'+(f"{monthly_price(r)/10000:g}万円" if r.get('rent') else '家賃未確認')+'｜'+r['key'][:8],key='individual_listing')
                st.markdown(listing_card(chosen,chosen['key'] in state.new_saved_keys),unsafe_allow_html=True)
        if rows:
            with st.expander('取得できた情報をすべて表で見る（未確認も保持）'):
                st.dataframe([{'物件ID':r.get('property_id','未記録'),'家賃（万円）':float(r['rent'])/10000 if r.get('rent') else None,'間取り':r.get('layout'),'住所（推定）':r.get('address'),'データ取得日時':acquisition_time_jst(r.get('fetched_at'))} for r in physical_units(rows)],hide_index=True)
    with tabs[1]:
        automatic_collection_panel()
        st.subheader('Supabaseに保存した物件')
        st.caption('保存物件をまとめて読み込みます。範囲外の物件も保持し、地図を動かすと表示できます。地図操作中の自動読み込みは行いません。')
        if st.button('保存物件をすべて読み込む',key='new_load',use_container_width=True,disabled=bool(active_job() and not active_job().snapshot()['finished'])):
            try:start_saved_load(bounds,state.get('address_point_cache'))
            except AppError as exc:st.error(str(exc))
        saved_load_progress()
        if state.get('new_notice'): st.success(state.new_notice)
        st.caption('DBには家賃・間取り・詳細住所（推定）・データ取得日時を保存します。読み込み時に住所を一時的に座標化して地図へ色付けし、その座標は保存しません。')
        st.download_button('現在の物件データをCSVで保存',csv_bytes(state.new_units),'sumai_rebuild_units.csv','text/csv',use_container_width=True)
        upload=st.file_uploader('このアプリのCSVを追加する',type=['csv'])
        if st.button('CSVの物件をSupabaseへ保存',disabled=upload is None or bool(active_job() and not active_job().snapshot()['finished']),key='new_import'):
            try:
                rows=read_csv(upload.getvalue());saved=Database().save_units(rows)
                state.new_map_loaded=False;state.new_units=[];state.new_saved_keys=[]
                state.new_notice=f'CSVから{len(saved)}件を保存しました。地図へ色付けする場合は保存データを読み込んでください。';st.rerun()
            except AppError as exc: st.error(str(exc))
        if st.button('画面上の未保存物件を再保存',key='new_retry_save',disabled=not state.new_units or bool(active_job() and not active_job().snapshot()['finished'])):
            try:
                saved=Database().save_units(state.new_units);state.new_saved_keys=list(set(state.new_saved_keys)|saved)
                state.new_notice=f'{len(saved)}件の保存を確認しました。';st.rerun()
            except AppError as exc: st.error(str(exc))
        if st.button('検索履歴を読み込む',key='new_history_load'):
            try: state.new_history=Database().history()
            except AppError as exc: st.error(str(exc))
        if state.get('new_history'):
            st.dataframe([{'開始（UTC）':r['started_at'],'結果':r['status'],'表示範囲':str(r['conditions'].get('bounds','')),
                           '保存確認':r['summary'].get('saved',0)} for r in state.new_history],hide_index=True)
        if state.get('new_history'):
            chosen=st.selectbox('作業ログを取り出す検索',state.new_history,format_func=lambda r:r['started_at']+'｜'+r['status'],key='diagnostic_history')
            if st.button('選んだ検索の詳細作業ログを読み込む',key='diagnostic_load'):
                try:
                    state.saved_diagnostics=Database().load_diagnostics(chosen['id'])
                    if not state.saved_diagnostics:st.info('この検索には保存された詳細ログがありません。v30以降で再検索してください。')
                except AppError as exc:st.error(str(exc))
        if state.get('saved_diagnostics'):diagnostic_downloads(state.saved_diagnostics,'saved')
        with st.expander('前回のログと比較して改善を確認'):
            baseline=st.file_uploader('前回の解析用JSONログを選択',type=['json'],key='diagnostic_baseline')
            if baseline:
                try:
                    old=json.loads(baseline.getvalue().decode('utf-8-sig')).get('events')
                    if not isinstance(old,list) or not all(isinstance(e,dict) for e in old):raise ValueError()
                    compare_target=st.radio('比較する今回のログ',['現在の検索','読み込んだ保存ログ'],key='diagnostic_compare_target')
                    current=(active_job().audit.records() if active_job() else []) if compare_target=='現在の検索' else state.get('saved_diagnostics',[])
                    if current:
                        st.dataframe(compare_logs(old,current),hide_index=True)
                        old_start=next((e.get('details',{}) for e in old if e.get('code')=='start'),{})
                        new_start=next((e.get('details',{}) for e in current if e.get('code')=='start'),{})
                        if old_start.get('bounds')!=new_start.get('bounds') or old_start.get('providers')!=new_start.get('providers'):st.info('表示範囲・取得元が異なります。差分だけで改善効果を判断できません。')
                        st.caption('差分はログの観測件数です。精度は同じ物件の正解データとの照合で確認してください。')
                    else:st.info('比較対象として現在の検索ログまたは保存ログを読み込んでください。')
                except (ValueError,UnicodeError,AttributeError):st.error('このアプリからダウンロードした解析用JSONログを選択してください。')
    with tabs[2]:
        st.subheader('通勤の目安')
        options=physical_units([r for r in state.new_units if has_point(r) and r.get('coordinate_precision')!='town'])
        if options:
            i=st.selectbox('出発する物件',range(len(options)),format_func=lambda i:options[i]['title'])
            destination=st.selectbox('勤務先の最寄り駅',list(STATIONS),key='new_destination')
            walk=st.number_input('勤務先までの徒歩（分）',min_value=0,max_value=60,value=8)
            r=options[i];nearest=min(STATIONS,key=lambda s:meters((r['latitude'],r['longitude']),STATIONS[s]))
            route=commute(nearest,destination)
            home=math.ceil(meters((r['latitude'],r['longitude']),STATIONS[nearest])*1.25/80)
            st.metric('ドアから勤務先までの概算',f"約{math.ceil(home+route['minutes']+walk+(3 if route['path'] else 0))}分")
            st.write(f"登録駅で最も近い{nearest}駅まで徒歩約{home}分｜乗り換え{route['transfers']}回")
            for a,b,line in route['path']: st.caption(f'{a} → {b}（{line}）')
            st.link_button('実際の経路・時刻をGoogleマップで確認','https://www.google.com/maps/dir/?'+urlencode({'api':1,'origin':f"{r['latitude']},{r['longitude']}",'destination':f'{STATIONS[destination][0]},{STATIONS[destination][1]}','travelmode':'transit'}))
            used=list(dict.fromkeys(line for _,_,line in route['path']))
            if used:
                st.dataframe([{'利用路線':line,'混雑率':str(CROWD[line][0])+'%','公表区間':CROWD[line][1],'調査時間帯':CROWD[line][2]} for line in used],hide_index=True)
                st.caption('国土交通省2025年度の代表区間・最混雑1時間の公表値。選択した経路・方向・時刻の混雑予測ではありません。')
                st.link_button('混雑率の原資料',CROWD_SOURCE)
            st.caption('登録41駅・11路線による概算。時刻表・直通運転・道路の徒歩経路は使用していません。徒歩は直線距離×1.25、列車は距離から推定、乗り換えは5分です。')
        else: st.info('物件を検索するか、保存物件を読み込むと通勤を比較できます。')
        st.subheader('地図の中心付近の施設')
        kind=st.selectbox('施設の種類',['スーパー','コンビニ','公園','病院'])
        if st.button('地図の中心から1kmの施設を地図に追加',key='new_facilities_fetch',disabled=not bounds):
            try:
                state.new_facilities=fetch_facilities(center,kind);state.new_facility_center=center
                saved=Database().save_places(state.new_facilities,kind)
                state.new_notice=f'{len(saved)}施設を取得し、Supabaseへの保存を確認しました。';st.rerun()
            except AppError as exc: st.error(str(exc))
        if st.button('保存した周辺施設を読み込む',key='new_places_load'):
            try:
                state.new_facilities=Database().load_places(center,kind);state.new_facility_center=center
                state.new_notice=f'{len(state.new_facilities)}施設を読み込みました。';st.rerun()
            except AppError as exc: st.error(str(exc))
        st.link_button('Googleマップで施設を探す','https://www.google.com/maps/search/?'+urlencode({'api':1,'query':f'{center[0]},{center[1]} '+kind}))
        st.caption('OpenStreetMapに登録された施設。登録漏れや位置の誤差がある場合があります。')
    with tabs[3]:
        st.subheader('新しいアプリの初期設定')
        st.write('1．SupabaseのSQL Editorに、以下のSQLを貼り付けてRunを押してください。')
        st.code(SQL,language='sql')
        st.download_button('セットアップSQLをダウンロード',SQL,'supabase_rebuild_01.sql','text/plain')
        st.write('2．StreamlitのSettings → Secretsに、接続先とサーバー用のSecret keyを設定してください。')
        st.code('SUPABASE_URL = "https://プロジェクトID.supabase.co"\nSUPABASE_SECRET_KEY = "sb_secret_から始まるキー"\nSUPABASE_NAMESPACE = "sumai-compass"',language='toml')
        st.write('物件取得にプロキシを使用する場合は、同じSecretsへ追加してください。Supabase・地名取得には適用しません。')
        st.code('RENTAL_HTTP_PROXIES = ["http://ユーザー:パスワード@ホスト:ポート", "http://別のユーザー:パスワード@別のホスト:ポート"]',language='toml')
        try:st.caption('設定済みプロキシ：'+str(len(rental_network_settings()['proxies']))+'経路')
        except AppError as exc:st.error(str(exc))
        st.caption('追加対策：同じ経路で公開トップページを確認してCookieを引き継ぎ、物件サイトごとに同時通信2件・通信開始は最低1秒間隔で取得します。403の経路とURLは60秒休止し、別の公開条件ページも確認します。')
        st.caption('利用するプロキシサービスの接続URLを設定してください。設定済みの経路で403・空応答・通信失敗が出た場合は別の経路へ切り替えます。認証情報は作業ログへ出力しません。')
        st.write('3．接続確認が成功したら、「住まいを探す」の地図を動かして検索してください。')
        if st.button('新しい保存先の接続を確認',key='new_check'):
            try: Database().check();st.success('物件・検索履歴の新しい保存先に接続できました。')
            except AppError as exc: st.error(str(exc))
        st.caption('旧アプリの物件や検索状態を使用しません。旧テーブルのデータは削除しません。')
        st.caption(f'実行中の版：{BUILD}')
    with st.expander('取得・集計の範囲'):
        st.write('HOMESは2つの間取り群で検索します。SUUMOは詳細ページで間取りを再取得し、マンション・築15年以内・1LDK/2K/2DKまたは2LDK/3K/3DKだけを採用します。SUUMOの位置は「地図・周辺環境」の物件マーカー、HOMESの位置は「地図を見る」の画像ピンから取得します。地図由来の詳細住所を推定できない物件は保存しません。')
        st.write('データベースへ保存する募集項目は家賃・間取り・詳細住所（推定）・データ取得日時です。取得日時はUTCで保存し、画面では日本時間で表示します。管理費・面積・築年数・画像・緯度経度・掲載URLは募集データとして保存しません。')
        st.write('募集情報は既存SupabaseのJSON保存領域へ保存するため、追加SQLは不要です。schema 4・5の保存データを読み込みます。旧データの取得日時は未記録と表示します。')
        st.write('SUUMOは物件マーカー座標、HOMESは画像ピン座標を起点にし、国土地理院の住居表示住所データから街区符号・基礎番号を最近傍推定します。一覧・詳細ページの所在地文字列から住所や位置を補完しません。')
        st.write('地図範囲内の居住地名タイルと100m間隔の地点・範囲の端から地名・丁目を判定します。候補数・物件数・ページ数による打ち切りは行いません。通信失敗やページ送りの異常は未完了として表示します。掲載サイト側の非公開情報・取得制限や、地名データの欠落は取得できません。')
        st.write('検索中は取得した物件の座標を使って地図へ逐次反映します。保存データ読込時は住所から座標を一時生成します。座標はDBへ保存しません。')
        st.write('検索と保存はサーバーのバックグラウンドで実行し、画面は1秒ごとに進捗を表示します。サーバーの休止・再起動を越えて実行することはできません。募集終了物件の自動削除は行いません。')
        st.caption('地名取得の代替経路：Geolonia Japanese Addresses v2（デジタル庁アドレス・ベース・レジストリ由来、CC BY 4.0）。町代表点を使って近隣自治体の全町丁目を検索します。地名データの欠落や境界の完全な網羅は保証できません。')
        st.link_button('町丁目データの出典・ライセンス','https://github.com/geolonia/japanese-addresses-v2')


if __name__=='__main__':
    main()
