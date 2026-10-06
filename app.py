"""住まいコンパス / REBUILD 01 — complete independent implementation.
Run: streamlit run app.py
Dependencies: streamlit>=1.50,<2, requests>=2.32,<3, beautifulsoup4>=4.13,<5, folium>=0.18,<1, streamlit-folium==0.24.0.
Persistent storage: Supabase housing_units_v1, housing_searches_v1, housing_places_v1.
Search and verified storage run on server worker threads; UI polls snapshots.
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
import queue
import re
import statistics
import threading
import time
import unicodedata
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlparse, parse_qs, quote

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium
from bs4 import BeautifulSoup

BUILD = "REBUILD-01-v28"
DEFAULT_CENTER = (35.7303,139.711)
UNIT_TABLE = "housing_units_v1"
SEARCH_TABLE = "housing_searches_v1"
PLACE_TABLE = "housing_places_v1"
GROUPS = {"1K・1L・1DK": ("1K", "1L", "1DK"), "1LDK・2DK": ("1LDK", "2DK"),
          "2LDK・3DK": ("2LDK", "3DK"), "3LDK": ("3LDK",)}
LAYOUTS = tuple(x for group in GROUPS.values() for x in group)
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
BANDS = (150000,200000,225000,250000,275000,300000,350000)
COLORS = ("#326b8b","#318a9b","#48a598","#8bb078","#bdb45c","#d89854","#cc7050","#aa4c56")
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


def bounded_results(items,fn,workers=6):
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


def valid_unit(row):
    try:
        return (row['layout'] in LAYOUTS and row['structure']=='SRC' and 0<=int(row['age'])<=20
                and 34<=float(row['latitude'])<=37 and 138<=float(row['longitude'])<=141
                and math.isfinite(float(row['area'])) and 0<float(row['area'])<=1000
                and 0<int(row['rent'])<=10000000 and 0<=int(row['fees'])<=10000000
                and bool(row['key']) and safe_url(row['listing_url'],row['provider']))
    except (KeyError,ValueError,TypeError): return False


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
        self.session=requests.Session()
    def call(self,method,table,params=None,data=None,prefer=None):
        if table not in (UNIT_TABLE,SEARCH_TABLE,PLACE_TABLE): raise AppError('保存先が不正です。')
        headers=dict(self.headers)
        if prefer: headers['Prefer']=prefer
        try:
            response=self.session.request(method,self.url+'/rest/v1/'+table,params=params,json=data,
                                          headers=headers,timeout=(4,15),allow_redirects=False)
        except requests.RequestException: raise AppError('Supabaseに接続できません。検索結果は画面に保持されます。') from None
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
    def save_units(self,rows):
        unique={r['key']:r for r in rows if valid_unit(r)}
        if len(unique)!=len({r.get('key') for r in rows}): raise AppError('保存対象の物件情報を確認できません。')
        if not unique: return set()
        payload=[dict(r,namespace=self.namespace) for r in unique.values()]
        result=self.call('POST',UNIT_TABLE,{'on_conflict':'namespace,key','select':'key'},payload,
                         'resolution=merge-duplicates,return=representation')
        saved={r.get('key') for r in result if isinstance(r,dict)} if isinstance(result,list) else set()
        if not set(unique)<=saved: raise AppError('Supabaseから保存完了の確認が得られません。')
        return set(unique)
    def save_search(self,search):
        result=self.call('POST',SEARCH_TABLE,{'on_conflict':'namespace,id','select':'id'},
                         [dict(search,namespace=self.namespace)],'resolution=merge-duplicates,return=representation')
        if not isinstance(result,list) or not any(x.get('id')==search['id'] for x in result if isinstance(x,dict)):
            raise AppError('検索履歴の保存を確認できません。')
    def load_units(self,bounds,layouts):
        s,w,n,e=bounds;out=[];offset=0
        while True:
            params={'namespace':'eq.'+self.namespace,'select':'*','order':'key.asc','limit':300,'offset':offset,
                    'and':f'(latitude.gte.{s},latitude.lte.{n},longitude.gte.{w},longitude.lte.{e})',
                    'layout':'in.('+','.join(layouts)+')','structure':'eq.SRC'}
            rows=self.call('GET',UNIT_TABLE,params)
            if not isinstance(rows,list): raise AppError('保存物件を読み取れません。')
            if not rows: break
            for row in rows:
                if row.get('built_ym'):
                    row=dict(row);row['age'],_=age_info(row['built_ym'].replace('-', '年')+'月')
                elif row.get('fetched_at'):
                    try:
                        stamp=datetime.fromisoformat(row['fetched_at'].replace('Z','+00:00'))
                        row=dict(row);row['age']=int(row['age'])+max(0,datetime.now(timezone.utc).year-stamp.year)
                    except (ValueError,TypeError):
                        continue
                if valid_unit(row) and in_rectangle((row['latitude'],row['longitude']),bounds): out.append(row)
            offset+=len(rows)
        return list({r['key']:r for r in out}.values())
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
        rows=self.call('GET',SEARCH_TABLE,{'namespace':'eq.'+self.namespace,'select':'*','order':'started_at.desc','limit':20})
        if not isinstance(rows,list): raise AppError('検索履歴を読み取れません。')
        return rows


class PublicWeb:
    def __init__(self):
        self.local=threading.local()
        self.headers={'User-Agent':'SumaiCompassRebuild/1.0 (personal rental research)', 'Accept-Language':'ja'}
        self.robots={};self.lock=threading.Lock()
    def session(self):
        if not hasattr(self.local,'session'): self.local.session=requests.Session()
        return self.local.session
    def fetch(self,url,method='GET',**kwargs):
        allowed=('www.homes.co.jp','suumo.jp','mreversegeocoder.gsi.go.jp','msearch.gsi.go.jp','maps.gsi.go.jp','cyberjapandata.gsi.go.jp','japanese-addresses-v2.geoloniamaps.com','geolonia.github.io')
        u=urlparse(url)
        if u.scheme!='https' or u.hostname not in allowed or u.username or u.password: raise AppError('取得先が不正です。')
        try:
            response=self.session().request(method,url,headers=self.headers,timeout=(4,12),allow_redirects=False,**kwargs)
            for _ in range(3):
                if response.status_code not in (301,302,303,307,308): break
                target=urljoin(response.url,response.headers.get('Location',''))
                v=urlparse(target)
                if v.scheme!='https' or v.hostname!=u.hostname or not v.path.startswith(u.path.rsplit('/',1)[0]):
                    raise AppError('取得先が別のページに移動しました。')
                response=self.session().get(target,headers=self.headers,timeout=(4,12),allow_redirects=False)
            response.raise_for_status()
        except requests.Timeout: raise AppError(f'{u.hostname}：接続または応答がタイムアウトしました。') from None
        except requests.HTTPError as exc: raise AppError(f'{u.hostname}：HTTP {exc.response.status_code}（公開ページの取得失敗）。') from None
        except requests.ConnectionError: raise AppError(f'{u.hostname}：接続できません（DNS・ネットワーク・接続先を確認）。') from None
        except requests.RequestException: raise AppError(f'{u.hostname}：通信処理に失敗しました。') from None
        if len(response.content)>8000000: raise AppError('公開ページの容量が上限を超えました。')
        response.encoding='utf-8' if 'gsi.go.jp' in u.hostname else response.apparent_encoding or 'utf-8'
        return response
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
        return parser.can_fetch(self.headers['User-Agent'],url)


def safe_url(url,provider):
    try:
        u=urlparse(url);host={'HOME’S':'www.homes.co.jp','SUUMO':'suumo.jp'}.get(provider)
        return bool(host and u.scheme=='https' and u.hostname==host and not u.username and not u.password
                    and u.port in (None,443) and u.path.startswith('/chintai/'))
    except (ValueError,TypeError): return False


def municipalities(web):
    text=web.fetch('https://maps.gsi.go.jp/js/muni.js').text
    table={}
    for code,value in re.findall(r'(?:GSI\.MUNI_ARRAY|muniArray)\["(\d+)"\]\s*=\s*[\'"]([^\'";]+)',text):
        fields=value.split(',')
        if len(fields)>=4: table[code]=(fields[0],fields[1],fields[3])
    if not table: raise AppError('国土地理院の地域一覧を読み取れません。')
    return table


def reverse(web,point,munis):
    if getattr(web,'reverse_unavailable',False): return None
    data=web.fetch('https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress',
                   params={'lat':point[0],'lon':point[1]}).json().get('results',{})
    code=str(data.get('muniCd',''));town=str(data.get('lv01Nm',''))
    if not code or not town or code not in munis: return None
    pref,prefecture,city=munis[code]
    return dict(code=code,pref=pref,town=town,label=prefecture+city+town,point=point)


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
    for tile,points,error in bounded_results(tiles,label_tile):
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
    for point,region,error in bounded_results(points,identify):
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
    if cached and time.monotonic()-cached[0]<86400: return cached[1]
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
    for city,rows,error in bounded_results(cities,towns):
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
    for script in soup.select('script[type="application/ld+json"]'):
        try: data=json.loads(script.get_text())
        except ValueError: continue
        for node in json_nodes(data):
            geo=node.get('geo')
            if isinstance(geo,dict):
                try: candidates.append((float(geo['latitude']),float(geo['longitude']),'掲載JSON-LD',3))
                except (ValueError,TypeError,KeyError): pass
    pattern=r'["\']lat(?:itude)?["\']\s*[:=]\s*["\']?(3[4-7]\.\d+)["\']?.{0,120}?["\'](?:lng|lon|longitude)["\']\s*[:=]\s*["\']?(1(?:3[89]|40)\.\d+)'
    for match in re.finditer(pattern,text,re.S|re.I): candidates.append((float(match[1]),float(match[2]),'掲載地図',2))
    reverse_pattern=r'["\'](?:lng|lon|longitude)["\']\s*[:=]\s*["\']?(1(?:3[89]|40)\.\d+)["\']?.{0,120}?["\']lat(?:itude)?["\']\s*[:=]\s*["\']?(3[4-7]\.\d+)'
    for match in re.finditer(reverse_pattern,text,re.S|re.I):
        candidates.append((float(match[2]),float(match[1]),'掲載地図',2))
    for match in re.finditer(r'/@(3[4-7]\.\d+),(1(?:3[89]|40)\.\d+)',text):
        candidates.append((float(match[1]),float(match[2]),'掲載地図',2))
    if hint: candidates.append((*hint,'HOME’S検索地図',2))
    return candidates


def locate(web,soup,text,address,bounds,munis,hint=None):
    candidates=[p for p in coordinate(soup,text,hint) if in_rectangle(p[:2],bounds)]
    method=''
    if candidates:
        lat,lng,method,_=max(candidates,key=lambda p:(p[3],-meters(bounds_center(bounds),p[:2])))
    elif re.search(r'\d+(?:丁目|番|号)|\d+-\d+',normal(address)):
        data=web.fetch('https://msearch.gsi.go.jp/address-search/AddressSearch',params={'q':address}).json()
        candidates=[]
        for item in data if isinstance(data,list) else []:
            try:
                lng,lat=map(float,item['geometry']['coordinates'])
                title=normal(item.get('properties',{}).get('title',''))
                if in_rectangle((lat,lng),bounds) and title and (address_key(title) in address_key(address) or address_key(address) in address_key(title)):
                    candidates.append((lat,lng))
            except (TypeError,ValueError,KeyError): continue
        if not candidates: return None
        lat,lng=candidates[0];method='住所検索（掲載座標なし）'
    else: return None
    try: region=reverse(web,(lat,lng),munis)
    except Exception: region=None
    map_address=region['label'] if region else ''
    a,b=address_key(address),address_key(map_address)
    if region and a:
        town_base=re.sub(r'\d+丁目$','',address_key(region['town']))
        if town_base and town_base not in a: return None
    match='未判定' if not a or not b else '一致' if a in b or b in a else '一部一致' if os.path.commonprefix([a,b]) and len(os.path.commonprefix([a,b]))>=5 else '不一致'
    # A known address conflict is not treated as a verified building location.
    if match=='不一致': return None
    return dict(latitude=lat,longitude=lng,location_method=method,map_address=map_address,address_match=match)


def create_unit(provider,url,title,address,layout,rent,fees,area,floor,age,built_ym,location):
    row=dict(key=hashlib.sha256(url.encode()).hexdigest(),title=str(title or '掲載物件')[:200],address=str(address or '')[:200],
             layout=normal(layout),rent=rent,fees=fees,area=area,floor=str(floor or '')[:80],structure='SRC',age=age,
             built_ym=built_ym,provider=provider,listing_url=url,fetched_at=utc_now(),**location)
    return row if valid_unit(row) else None


def homes_detail(web,url,bounds,munis,hint):
    text=web.fetch(url).text;soup=BeautifulSoup(text,'html.parser');recognized=False
    for script in soup.select('script[type="application/ld+json"]'):
        try: data=json.loads(script.get_text())
        except ValueError: continue
        for listing in json_nodes(data):
            if listing.get('@type')!='RealEstateListing': continue
            entity=listing.get('mainEntity') or {};offer=listing.get('offers') or {}
            if not isinstance(entity,dict) or not isinstance(offer,dict): continue
            if offer.get('priceCurrency') not in (None,'JPY'): continue
            if offer.get('availability') not in (None,'https://schema.org/InStock','http://schema.org/InStock'): continue
            attrs={x.get('name'):x.get('value') for x in entity.get('additionalProperty',[]) if isinstance(x,dict)}
            if attrs.get('間取り') and (attrs.get('建物構造') or attrs.get('構造')): recognized=True
            costs={x.get('name'):x.get('value') for x in offer.get('additionalProperty',[]) if isinstance(x,dict)}
            if not is_src(attrs.get('建物構造') or attrs.get('構造') or labeled(soup,('建物構造','構造'))): continue
            age,ym=age_info(attrs.get('築年月') or attrs.get('築年数') or labeled(soup,('築年月','築年数')))
            if age is None or not 0<=age<=20 or normal(attrs.get('間取り')) not in LAYOUTS: continue
            fees=costs.get('管理費等',costs.get('管理費'))
            if fees is None: raise AppError('HOME’S詳細ページの管理費を読み取れません。保存額を確認できません。')
            addr=entity.get('address') or {}
            address=''.join(str(addr.get(x,'')) for x in ('addressRegion','addressLocality','streetAddress')) if isinstance(addr,dict) else ''
            location=locate(web,soup,text,address,bounds,munis,hint)
            if not location: continue
            try:
                return create_unit('HOME’S',url,entity.get('name') or listing.get('name'),address,attrs.get('間取り'),
                                   amount(offer.get('price')),amount(fees),float((entity.get('floorSize') or {}).get('value',0)),
                                   entity.get('floorLevel'),age,ym,location)
            except (AppError,TypeError,ValueError): continue
    if recognized: return None
    # Published HTML tables remain usable when the page has no RealEstateListing JSON-LD.
    structure=labeled(soup,('建物構造','構造'))
    layout=normal(labeled(soup,('間取り',)))
    if not structure or not layout:
        raise AppError('HOME’S詳細ページの構造・間取りを読み取れません。募集終了またはページ形式の変更を確認してください。')
    age,ym=age_info(labeled(soup,('築年月','築年数')))
    if not is_src(structure) or layout not in LAYOUTS or age is None or not 0<=age<=20: return None
    rent=labeled(soup,('賃料','家賃'));fees=labeled(soup,('管理費等','管理費','管理費・共益費'))
    address=labeled(soup,('所在地','住所'));area=labeled(soup,('専有面積','面積'))
    if not rent or not fees or not area: raise AppError('HOME’S詳細の賃料・管理費・面積を読み取れません。')
    match=re.search(r'\d+(?:\.\d+)?',normal(area))
    location=locate(web,soup,text,address,bounds,munis,hint)
    if not location or not match: return None
    title=soup.select_one('h1')
    return create_unit('HOME’S',url,title.get_text(' ',strip=True) if title else 'HOME’S掲載物件',address,layout,
                       amount(rent),amount(fees),float(match[0]),labeled(soup,('所在階','階数')),age,ym,location)


def homes_collect(web,region,bounds,munis,emit):
    tile_url='https://www.homes.co.jp/_ajax/map/realestate_article/tile/'
    info_url='https://www.homes.co.jp/_ajax/map/realestate_article/info_view/'
    if not all(web.permitted(u) for u in (tile_url,info_url)):
        raise AppError('HOME’Sの取得可否を確認できない、または自動取得が許可されていません。')
    tiles=tiles_in_bounds(bounds)
    base={'cond[houseageh]':'20','cond[freeword]':region['town'],'cond[fwtype]':'1','zoom':'15',
          'cond[mbg][3001]':'3001','cond[mbg][3002]':'3002','cond[mbg][3003]':'3003',
          'cond[monthmoneyroom]':'0','cond[monthmoneyroomh]':'0','cond[housearea]':'0','cond[houseareah]':'0',
          'cond[walkminutesh]':'0','cond[newdate]':'0','cond[exfreeword]':''}
    buildings={}
    for offset in range(0,len(tiles),6):
        emit('message',f'地図候補 {offset+1}〜{min(offset+6,len(tiles))}/{len(tiles)}タイル')
        params=dict(base)
        for i,(x,y) in enumerate(tiles[offset:offset+6]): params[f'tiles[{i}][x]']=x;params[f'tiles[{i}][y]']=y
        try:
            data=web.fetch(tile_url,'POST',data=params).json()
            if not isinstance(data,dict): raise AppError('HOME’S地図候補の応答形式を確認できません。')
        except (AppError,ValueError) as exc:
            emit('issue',str(exc));continue
        if not any(isinstance(group,dict) and isinstance(group.get('row_set'),list) for group in data.values()):
            emit('issue','HOME’S地図候補の建物一覧を読み取れません。応答形式を確認してください。');continue
        for group in data.values() if isinstance(data,dict) else []:
            for row in group.get('row_set',[]) if isinstance(group,dict) else []:
                try:
                    lat,lng=float(row['lat']),float(row['lon']);key=str(row['tykey'])
                    if re.fullmatch(r'[A-Za-z0-9]+',key) and in_rectangle((lat,lng),bounds): buildings[key]=(lat,lng)
                except (ValueError,TypeError,KeyError): continue
    emit('candidate',len(buildings));links={}
    for i,(key,point) in enumerate(sorted(buildings.items(),key=lambda x:meters(region['point'],x[1])),1):
        emit('message',f'建物候補の確認 {i}/{len(buildings)}')
        try: text=web.fetch(info_url,'POST',data=dict(base,**{'cond[tykey]':key})).text
        except AppError as exc: emit('issue',str(exc));continue
        for a in BeautifulSoup(text,'html.parser').select('a[href]'):
            url=urljoin('https://www.homes.co.jp',a['href']).split('#')[0]
            if safe_url(url,'HOME’S'): links[url]=point
    accepted=0
    for index,(url,hint) in enumerate(links.items(),1):
        emit('message',f'詳細確認 {index}/{len(links)}件');emit('detail',1)
        if not web.permitted(url): emit('issue','詳細ページの自動取得が許可されていません。');continue
        try: row=homes_detail(web,url,bounds,munis,hint)
        except AppError as exc: emit('issue',str(exc));row=None
        if row: emit('unit',row);accepted+=1
        else: emit('rejected',1)


def suumo_collect(web,region,bounds,munis,emit):
    search='https://suumo.jp/jj/chintai/ichiran/FR301FC001/'
    if not web.permitted(search): raise AppError('SUUMOの取得可否を確認できない、または自動取得が許可されていません。')
    candidates={};page=1;seen_pages=set()
    def verify_page(page_candidates):
        for index,(url,values) in enumerate(page_candidates.items(),1):
            emit('message',f'詳細確認 {index}/{len(page_candidates)}件');emit('detail',1)
            if not web.permitted(url): emit('issue','詳細ページの自動取得が許可されていません。');continue
            try:
                text=web.fetch(url).text;soup=BeautifulSoup(text,'html.parser')
                structure=labeled(soup,('構造','建物構造'))
                age,ym=age_info(labeled(soup,('築年月','築年数')))
                if not structure or age is None: raise AppError('SUUMO詳細ページの構造・築年月を読み取れません。募集終了またはページ形式の変更を確認してください。')
                if not is_src(structure) or not 0<=age<=20:
                    emit('rejected',1);continue
                location=locate(web,soup,text,values[1],bounds,munis)
                row=create_unit('SUUMO',url,*values,labeled(soup,('階建','所在階')),age,ym,location) if location else None
                if row: emit('unit',row)
                else: emit('rejected',1)
            except (AppError,ValueError,TypeError) as exc: emit('issue',str(exc));emit('rejected',1)
    while True:
        emit('message',f'検索一覧 {page}ページ目（件数上限なし）')
        params={'ar':'030','bs':'040','pc':'50','sc':region['code'],'ta':region['pref'],'fw2':region['town'],'page':page}
        soup=BeautifulSoup(web.fetch(search,params=params).text,'html.parser')
        page_candidates={}
        buildings=soup.select('div.cassetteitem')
        if not buildings:
            if any(t in soup.get_text() for t in ('該当する物件','該当物件','物件が見つかりません','0件')): break
            raise AppError(f'SUUMO {page}ページ目の一覧を確認できません。')
        signature=hashlib.sha256(str(buildings).encode()).hexdigest()
        if signature in seen_pages: raise AppError('SUUMOのページ送りが同じ一覧を返しました。全ページを確認できていません。')
        seen_pages.add(signature)
        for building in buildings:
            def text(selector,default=''):
                el=building.select_one(selector);return el.get_text(' ',strip=True) if el else default
            address=text('.cassetteitem_detail-col1')
            if address_key(region['town']) not in address_key(address): continue
            title=text('.cassetteitem_content-title','SUUMO掲載物件')
            for room in building.select('tr.js-cassette_link'):
                def field(selector):
                    el=room.select_one(selector);return el.get_text(' ',strip=True) if el else ''
                layout=field('.cassetteitem_madori')
                link=room.select_one('a[href*="/chintai/"]')
                if layout not in LAYOUTS or not link or room.select_one('.cassetteitem_price--administration') is None: continue
                url=urljoin('https://suumo.jp',link['href']).split('#')[0]
                if not safe_url(url,'SUUMO') or url in candidates: continue
                try:
                    match=re.search(r'\d+(?:\.\d+)?',normal(field('.cassetteitem_menseki')))
                    if not match: continue
                    rent=amount(field('.cassetteitem_price--rent'));fees=amount(field('.cassetteitem_price--administration'))
                    if rent<=0: continue
                    candidates[url]=(title,address,layout,rent,fees,float(match[0]))
                    page_candidates[url]=candidates[url]
                except AppError: continue
        emit('candidate',len(page_candidates));verify_page(page_candidates)
        next_pages=[]
        for a in soup.select('a[href]'):
            target=urlparse(urljoin(search,a['href']))
            if target.hostname!='suumo.jp' or target.path!=urlparse(search).path: continue
            try: number=int(parse_qs(target.query).get('page',['0'])[0])
            except ValueError: continue
            if number>page: next_pages.append(number)
        if not next_pages: break
        page=min(next_pages)


def search_all(db,conditions,screen,state=None):
    if state is None: state=st.session_state
    bounds=tuple(conditions['bounds']);started=time.monotonic()
    search=dict(id=hashlib.sha256((utc_now()+str(time.time_ns())).encode()).hexdigest(),status='started',
                started_at=utc_now(),finished_at=None,conditions=conditions,summary={})
    units={};saved=set();issues=[];logs=[]
    def log(message):
        logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        screen['log'].code('\n'.join(logs[-15:]),language=None)
    screen['status'].info('検索実行中｜保存先の接続・新しいテーブルを確認しています')
    db.check();db.save_search(search);log('保存先確認OK。新しい検索を開始しました。')
    web=PublicWeb()
    def region_update(stage,done,total,count,errors):
        if stage.startswith('地名取得先を切替'): log(stage)
        screen['bar'].progress(.05+.15*done/max(1,total),text=f'{stage} {done}/{total}')
        screen['status'].info(f'検索実行中｜{stage} {done}/{total}｜{count}地域｜取得失敗 {errors}件｜経過 {int(time.monotonic()-started)}秒')
    try:
        screen['status'].info('検索実行中｜国土地理院の地域情報を取得しています')
        regions,munis,region_errors=regional_tasks(web,bounds,region_update)
        if region_errors: issues.append(f'地域判定で{region_errors}地点を確認できませんでした。')
        search['conditions']['region_source']='Geolonia町丁目一覧' if getattr(web,'reverse_unavailable',False) else '国土地理院地名判定'
        log('地名取得完了｜'+search['conditions']['region_source']+'｜'+str(len(regions))+'地域')
        search['conditions']['regions']=[r['label'] for r in regions]
        state.new_regions=[r['label'] for r in regions]
        jobs=[(r,p) for r in regions for p in conditions['providers']]
        q=queue.Queue();active={};done=0;candidate=detail=rejected=0
        def work(index,region,provider):
            def emit(kind,value): q.put((index,kind,value))
            emit('message','候補検索を開始')
            collector=homes_collect if provider=='HOME’S' else suumo_collect
            try: collector(web,region,bounds,munis,emit)
            except Exception as exc: emit('issue',str(exc) if isinstance(exc,AppError) else f'取得処理エラー（{type(exc).__name__}）')
        with futures.ThreadPoolExecutor(max_workers=min(4,len(jobs))) as pool:
            pending={pool.submit(work,i,*job):i for i,job in enumerate(jobs)}
            completed=set()
            while pending or not q.empty():
                ready,_=futures.wait(pending,timeout=.35,return_when=futures.FIRST_COMPLETED) if pending else (set(),set())
                batch=[]
                while True:
                    try: index,kind,value=q.get_nowait()
                    except queue.Empty: break
                    label=jobs[index][0]['label']+'｜'+jobs[index][1]
                    if kind=='message' and index not in completed: active[index]=label+'｜'+str(value)
                    elif kind=='candidate': candidate+=int(value)
                    elif kind=='detail': detail+=int(value)
                    elif kind=='rejected': rejected+=int(value)
                    elif kind=='issue':
                        issues.append(label+'｜'+str(value));log(issues[-1])
                    elif kind=='unit':
                        units[value['key']]=value
                        if value['key'] not in saved: batch.append(value)
                if batch:
                    screen['status'].info(f'検索実行中｜確認した{len(batch)}件をSupabaseへ保存しています')
                    try:
                        for offset in range(0,len(batch),100): saved.update(db.save_units(batch[offset:offset+100]))
                    except AppError as exc: issues.append(str(exc));log('保存エラー｜'+str(exc))
                    # Browser session holds unconfirmed rows separately; no claim of persistence.
                    state.new_units=list(units.values())
                    state.new_saved_keys=list(saved)
                for f in ready:
                    index=pending.pop(f);completed.add(index);active.pop(index,None);done+=1
                    f.result();log(f'{done}/{len(jobs)}完了｜'+jobs[index][0]['label']+'｜'+jobs[index][1])
                screen['bar'].progress(.2+.75*done/max(1,len(jobs)),text=f'物件確認 {done}/{len(jobs)}タスク｜保存確認 {len(saved)}件')
                screen['status'].info(f'検索実行中｜{done}/{len(jobs)}タスク完了｜候補 {candidate}｜詳細 {detail}｜除外 {rejected}｜保存確認 {len(saved)}件｜経過 {int(time.monotonic()-started)}秒\n\n'+'\n\n'.join(active.values()))
        search['status']='partial' if issues else 'completed'
        search['summary']={'confirmed':len(units),'saved':len(saved),'candidate':candidate,'detail':detail,'rejected':rejected,
                           'issues':issues[-100:],'elapsed':int(time.monotonic()-started),'tasks':len(jobs)}
    except Exception as exc:
        search['status']='failed';issues.append(str(exc) if isinstance(exc,AppError) else f'検索エラー（{type(exc).__name__}）')
        search['summary']={'confirmed':len(units),'saved':len(saved),'issues':issues,'elapsed':int(time.monotonic()-started)}
    search['finished_at']=utc_now()
    try: db.save_search(search)
    except AppError as exc: issues.append('検索履歴｜'+str(exc));search['summary']['issues']=issues;search['status']='partial'
    screen['bar'].progress(1.,text='検索処理が終了しました')
    state.new_units=list(units.values())
    state.new_search=search
    state.new_saved_keys=list(saved)
    return search


def physical_units(rows):
    grouped={}
    for r in rows:
        fingerprint=(round(r['latitude'],5),round(r['longitude'],5),r['layout'],r.get('floor',''),
                     round(r['area'],1),r['rent'],r['fees'])
        grouped.setdefault(fingerprint,r)
    return list(grouped.values())


def mesh(rows,center,radius,interpolate,bounds=None):
    positions={}
    for r in physical_units(rows): positions.setdefault((round(r['latitude'],5),round(r['longitude'],5)),[]).append(r['rent']+r['fees'])
    buildings=[(p,statistics.median(v)) for p,v in positions.items()]
    cells={};step=250
    for point,price in buildings:
        y=round((point[0]-center[0])*111320/step);x=round((point[1]-center[1])*111320*math.cos(math.radians(center[0]))/step)
        cells.setdefault((x,y),[]).append(price)
    out=[];n=math.ceil(radius/step)
    for x in range(-n,n+1):
        for y in range(-n,n+1):
            p=(center[0]+y*step/111320,center[1]+x*step/(111320*math.cos(math.radians(center[0]))))
            if bounds is not None:
                if not in_rectangle(p,bounds): continue
            elif meters(center,p)>radius: continue
            values=cells.get((x,y));estimated=False
            if values: price=statistics.median(values);count=len(values)
            elif interpolate:
                nearby=[(meters(p,b),v) for b,v in buildings if meters(p,b)<=750]
                if len(nearby)<3: continue
                weighted=[(1/max(30,d)**2,v) for d,v in nearby]
                price=sum(w*v for w,v in weighted)/sum(w for w,_ in weighted);count=len(nearby);estimated=True
            else: continue
            cell_bounds=rectangle(p,step/2)
            out.append(dict(bounds=[[cell_bounds[0],cell_bounds[1]],[cell_bounds[2],cell_bounds[3]]],price=price,count=count,estimated=estimated))
    return out


def rental_map(rows,center,radius,cells,facilities):
    m=folium.Map(location=center,zoom_start=15,tiles=None,control_scale=True)
    folium.TileLayer('https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png',
        attr='国土地理院',name='地名の地図',max_zoom=18).add_to(m)
    rental_features(rows,cells,facilities).add_to(m)
    return m


def rental_features(rows,cells,facilities):
    m=folium.FeatureGroup(name='物件・家賃・施設')
    for c in cells:
        color=COLORS[sum(c['price']>cut for cut in BANDS)]
        tip=f"{c['price']/10000:.1f}万円/月｜{'近隣から推定' if c['estimated'] else '掲載額集計'}｜根拠 {c['count']}建物位置"
        folium.Rectangle(c['bounds'],color=color,weight=.4,fill=True,fill_color=color,
                         fill_opacity=.24 if c['estimated'] else .55,dash_array='4,3' if c['estimated'] else None,tooltip=tip).add_to(m)
    for r in physical_units(rows):
        tip=html.escape(r['title'])+f"｜{(r['rent']+r['fees'])/10000:g}万円"
        popup=(f"<b>{html.escape(r['title'])}</b><br>{html.escape(r['layout'])} / {r['area']:g}㎡<br>"
               f"総額 {(r['rent']+r['fees'])/10000:g}万円<br>{html.escape(r['address'])}<br>"
               f"{html.escape(r['location_method'])}<br><a href='{html.escape(r['listing_url'],quote=True)}' target='_blank' rel='noopener'>募集ページ</a>")
        folium.CircleMarker((r['latitude'],r['longitude']),radius=5,color='#fff',weight=1,fill=True,
                            fill_color=COLORS[sum(r['rent']+r['fees']>cut for cut in BANDS)],fill_opacity=1,
                            tooltip=tip,popup=folium.Popup(popup,max_width=280)).add_to(m)
    for f in facilities:
        folium.CircleMarker((f['lat'],f['lng']),radius=6,color='#fff',fill=True,fill_color='#203f39',fill_opacity=1,tooltip=html.escape(f['name'])).add_to(m)
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
    keys=['key','title','address','latitude','longitude','layout','rent','fees','area','floor','structure','age','built_ym',
          'location_method','map_address','address_match','provider','listing_url','fetched_at']
    file=io.StringIO();writer=csv.DictWriter(file,fieldnames=keys,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    return file.getvalue().encode('utf-8-sig')


def read_csv(content):
    if len(content)>10000000: raise AppError('CSVは10MB以下にしてください。')
    try:
        rows=[]
        for row in csv.DictReader(io.StringIO(content.decode('utf-8-sig'))):
            for key in ('latitude','longitude','area'): row[key]=float(row[key])
            for key in ('rent','fees','age'): row[key]=int(row[key])
            row['built_ym']=row.get('built_ym') or None
            if not valid_unit(row): raise ValueError()
            row['key']=hashlib.sha256(row['listing_url'].encode()).hexdigest()
            rows.append(row)
        if not rows: raise ValueError()
        return rows
    except (UnicodeError,ValueError,KeyError,TypeError,csv.Error): raise AppError('この新しいアプリから出力したCSVを指定してください。') from None


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
    providers=list(state.get('new_providers',[]))
    bounds=state.get('new_bounds')
    if not providers or not bounds:
        state.new_search={'status':'failed','summary':{'issues':['地図の表示範囲と取得元を確認してください。'],'saved':0,'confirmed':0}}
        return
    state.new_request=dict(bounds=list(bounds),providers=providers)



class WorkerState:
    def __init__(self):
        self.new_units=[];self.new_search=None;self.new_saved_keys=[];self.new_regions=[]

class SearchJob:
    def __init__(self,db,conditions):
        self.lock=threading.RLock();self.state=WorkerState();self.progress=0.;self.message='バックグラウンド検索を開始しています';self.text='';self.finished=False
        self.thread=threading.Thread(target=self.run,args=(db,conditions),daemon=True,name='housing-search')
    def run(self,db,conditions):
        job=self
        class Display:
            def progress(self,value,text=''):
                with job.lock: job.progress=value
            def info(self,value):
                with job.lock: job.message=value
            def code(self,value,language=None):
                with job.lock: job.text=value
        screen={k:Display() for k in ('bar','status','log')}
        try: search_all(db,conditions,screen,self.state)
        except Exception as exc:
            self.state.new_search={'status':'failed','conditions':conditions,'summary':{'issues':[str(exc) if isinstance(exc,AppError) else '検索処理エラー（'+type(exc).__name__+'）'],'saved':len(self.state.new_saved_keys),'confirmed':len(self.state.new_units)}}
        finally:
            with self.lock: self.finished=True
    def snapshot(self):
        with self.lock:
            return dict(progress=self.progress,message=self.message,log=self.text,finished=self.finished,
                        units=list(self.state.new_units),saved=list(self.state.new_saved_keys),result=self.state.new_search)

@st.cache_resource
def job_registry():
    return {},threading.RLock()

def active_job():
    jobs,lock=job_registry()
    with lock: return jobs.get(st.session_state.get('new_job_token'))

def start_search(conditions):
    jobs,lock=job_registry()
    token=st.session_state.setdefault('new_job_token',hashlib.sha256(os.urandom(32)).hexdigest())
    with lock:
        old=jobs.get(token)
        if old and not old.snapshot()['finished']: return
        # Secrets are read on the Streamlit thread, before launching a pure Python worker.
        job=SearchJob(Database(),conditions);jobs[token]=job
        # Release finished jobs from other sessions after two hours.
        for key,value in list(jobs.items()):
            if key!=token and value.finished and time.monotonic()-getattr(value,'created',time.monotonic())>7200: jobs.pop(key,None)
        job.created=time.monotonic();job.thread.start()
    st.session_state.new_search=None

@st.fragment(run_every='1s')
def background_status():
    job=active_job()
    if job is None: return
    snap=job.snapshot()
    st.session_state.new_units=snap['units'];st.session_state.new_saved_keys=snap['saved']
    if snap['finished']:
        st.session_state.new_search=snap['result']
        if not st.session_state.get('new_job_rendered')==id(job):
            st.session_state.new_job_rendered=id(job);st.rerun()
        return
    st.progress(min(.99,snap['progress']),text='バックグラウンドで検索・保存しています')
    st.info(snap['message'])
    if snap['log']: st.code(snap['log'],language=None)
    st.caption('画面を操作しても検索と保存は継続します。サーバーの休止・再起動では実行が終了します。')

def main():
    st.set_page_config(page_title='住まいコンパス｜新しい住まいを探す',page_icon='🏡',layout='wide',initial_sidebar_state='collapsed')
    st.markdown(CSS,unsafe_allow_html=True)
    st.markdown(f'<div class="hero"><span class="badge">SUMAI COMPASS · {BUILD}</span><h1>次の住まいを、地図から。</h1><p>掲載物件を確認し、家賃と暮らしやすさを比べます。<br>SRC・築20年以内の賃貸物件を対象にしています。</p></div>',unsafe_allow_html=True)
    state=st.session_state
    state.setdefault('new_units',[]);state.setdefault('new_search',None);state.setdefault('new_saved_keys',[])
    state.setdefault('new_facilities',[])
    request=state.pop('new_request',None)
    if request is not None:
        try: start_search(request)
        except AppError as exc: state.new_search={'status':'failed','summary':{'issues':[str(exc)],'saved':0,'confirmed':0}}
    preferences=state.get('new_preferences',{})
    tabs=st.tabs(['住まいを探す','保存データ','通勤・周辺施設','初期設定'])
    with tabs[0]:
        st.subheader('地図を動かして、探す地域を表示してください')
        st.caption('地図に見えている四角い範囲が検索対象です。駅名の選択や取得件数の上限はありません。')
        group=st.radio('間取り',list(GROUPS),index=list(GROUPS).index(preferences.get('new_group','1LDK・2DK')),horizontal=True,key='new_group')
        budget=st.number_input('月額の上限（管理費込み・万円）',min_value=1.,max_value=1000.,value=preferences.get('new_budget',50.),step=1.,key='new_budget')
        interpolate=st.checkbox('データが十分な場所は近隣家賃も推定する',value=preferences.get('new_interpolate',False),key='new_interpolate')
        bounds=state.get('new_bounds')
        center=bounds_center(bounds) if bounds else state.get('new_view_center',DEFAULT_CENTER)
        radius=bounds_radius(bounds) if bounds else 1500
        rows=[r for r in state.new_units if valid_unit(r) and r['layout'] in GROUPS[group]
              and (bounds is None or in_rectangle((r['latitude'],r['longitude']),bounds))]
        pins=[r for r in rows if r['rent']+r['fees']<=budget*10000]
        cells=mesh(rows,center,radius,interpolate,bounds) if bounds else []
        facilities=[f for f in state.new_facilities if bounds and in_rectangle((f['lat'],f['lng']),bounds)]
        data=st_folium(rental_map([],DEFAULT_CENTER,1500,[],[]),key='new_map',height=480,use_container_width=True,
            returned_objects=['bounds','zoom'],center=state.get('new_view_center',DEFAULT_CENTER),
            zoom=state.get('new_view_zoom',15),feature_group_to_add=rental_features(pins,cells,facilities),on_change=capture_viewport)
        # Initial component defaults are not real viewport bounds. Only the browser callback makes the search ready.
        bounds=state.get('new_bounds')
        if bounds:
            center=bounds_center(bounds);radius=bounds_radius(bounds)
            south,west,north,east=bounds
            st.caption(f'検索する表示範囲：緯度 {south:.5f}〜{north:.5f}／経度 {west:.5f}〜{east:.5f}')
        else: st.info('地図の表示範囲を受信しています。表示後に地図を少し動かしてください。対象は東京と周辺地域です。')
        providers=st.multiselect('物件の取得元',['HOME’S','SUUMO'],default=preferences.get('new_providers',['HOME’S','SUUMO']),key='new_providers')
        st.button('表示中の地名から全件検索・保存',type='primary',use_container_width=True,
                  on_click=remember_search,key='new_start',disabled=not bounds or not providers or bool(active_job() and not active_job().snapshot()['finished']))
        st.caption('検索開始時の表示範囲を固定し、地名・丁目ごとに取得元の掲載一覧を最後まで確認します。予算・間取りの表示設定は取得件数を制限しません。')
        background_status()
        result=state.new_search
        if result:
            summary=result['summary'];saved=summary.get('saved',0);confirmed=summary.get('confirmed',0)
            if result['status']=='completed': st.success(f'検索完了｜確認 {confirmed}件・保存確認 {saved}件')
            elif result['status']=='partial': st.error(f'検索終了｜確認 {confirmed}件・保存確認 {saved}件。一部の取得・保存に失敗しました。')
            else: st.error('検索を完了できませんでした。以下の原因を確認してください。')
            for issue in summary.get('issues',[])[:8]: st.write('・'+issue)
            if summary.get('elapsed') is not None: st.caption(f"所要 {summary['elapsed']}秒｜詳細確認 {summary.get('detail',0)}件｜除外 {summary.get('rejected',0)}件")
            if result.get('conditions',{}).get('regions'):
                with st.expander('今回検索した地名・丁目'):
                    for label in result['conditions']['regions']: st.write(label)
        legend=''.join(f'<span style="display:inline-block;margin:4px 10px 4px 0;color:#203f39;font-size:12px"><b style="color:{color}">■</b> {label}</span>' for color,label in zip(COLORS,['15万円以下','15〜20万円','20〜22.5万円','22.5〜25万円','25〜27.5万円','27.5〜30万円','30〜35万円','35万円超']))
        st.markdown(legend,unsafe_allow_html=True)
        st.caption('濃い区画：掲載額の集計／薄い破線：750m以内の3建物位置以上から推定。区画は250m。家賃帯の集計は月額上限で絞る前のデータです。')
        c1,c2,c3=st.columns(3)
        c1.metric('条件内の募集',len(physical_units(pins)))
        c2.metric('月額の中央値',f"{statistics.median([r['rent']+r['fees'] for r in pins])/10000:.1f}万円" if pins else '—')
        c3.metric('集計した区画',len(cells))
        if not pins: st.info('この範囲・間取り・予算に表示できる物件がありません。検索するか、保存データを読み込んでください。')
        for r in sorted(physical_units(pins),key=lambda x:x['rent']+x['fees']):
            persisted=r['key'] in state.new_saved_keys
            st.markdown(f'<div class="unit"><h3>{html.escape(r["title"])}</h3><b>{(r["rent"]+r["fees"])/10000:g}万円 / 月</b> · {html.escape(r["layout"])} · {r["area"]:g}㎡ · 築{r["age"]}年<p>{html.escape(r["address"])}</p><a href="{html.escape(r["listing_url"],quote=True)}" target="_blank" rel="noopener">募集ページを開く ↗</a><p style="font-size:12px">{html.escape(r["provider"])} · {html.escape(r["location_method"])} · {"保存確認済み" if persisted else "画面上のみ・保存未確認"}</p></div>',unsafe_allow_html=True)
        if pins:
            with st.expander('すべての物件を表で見る'):
                st.dataframe([{'物件名':r['title'],'間取り':r['layout'],'総額（万円）':(r['rent']+r['fees'])/10000,'面積':r['area'],
                               '築年数':r['age'],'掲載住所':r['address'],'地図判定住所':r['map_address'],'住所照合':r['address_match'],
                               '取得元':r['provider'],'掲載ページ':r['listing_url']} for r in physical_units(pins)],hide_index=True,
                             column_config={'掲載ページ':st.column_config.LinkColumn()})
    with tabs[1]:
        st.subheader('Supabaseに保存した物件')
        st.caption('自動読み込みは行いません。地図に表示中の範囲にある物件データを読み込みます。')
        if st.button('表示範囲の保存物件を読み込む',key='new_load',use_container_width=True,disabled=not bounds):
            try:
                with st.spinner('Supabaseから読み込んでいます'):
                    rows=Database().load_units(bounds,LAYOUTS)
                state.new_units=rows;state.new_saved_keys=[r['key'] for r in rows];state.new_search=None
                state.new_notice=f'{len(rows)}件をSupabaseから読み込みました。';st.rerun()
            except AppError as exc: st.error(str(exc))
        if state.get('new_notice'): st.success(state.new_notice)
        st.download_button('現在の物件データをCSVで保存',csv_bytes(state.new_units),'sumai_rebuild_units.csv','text/csv',use_container_width=True)
        upload=st.file_uploader('このアプリのCSVを追加する',type=['csv'])
        if st.button('CSVの物件をSupabaseへ保存',disabled=upload is None,key='new_import'):
            try:
                rows=read_csv(upload.getvalue());saved=Database().save_units(rows)
                state.new_units=list({r['key']:r for r in state.new_units+rows}.values())
                state.new_saved_keys=list(set(state.new_saved_keys)|saved)
                state.new_notice=f'CSVから{len(saved)}件を保存しました。';st.rerun()
            except AppError as exc: st.error(str(exc))
        if st.button('画面上の未保存物件を再保存',key='new_retry_save',disabled=not state.new_units):
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
    with tabs[2]:
        st.subheader('通勤の目安')
        options=physical_units(state.new_units)
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
        st.write('3．接続確認が成功したら、「住まいを探す」の地図を動かして検索してください。')
        if st.button('新しい保存先の接続を確認',key='new_check'):
            try: Database().check();st.success('物件・検索履歴の新しい保存先に接続できました。')
            except AppError as exc: st.error(str(exc))
        st.caption('旧アプリの物件や検索状態を使用しません。旧テーブルのデータは削除しません。')
        st.caption(f'実行中の版：{BUILD}')
    with st.expander('取得・集計の範囲'):
        st.write('公開ページの掲載情報を取得し、SRC・築20年以内・対象間取り・位置を確認した物件を保存します。架空の物件や家賃は生成しません。')
        st.write('掲載地図座標を優先し、番地のある住所は一致する住所検索結果で補完します。住所と地図判定に明確な不一致がある物件は除外します。')
        st.write('地図範囲内の居住地名タイルと100m間隔の地点・範囲の端から地名・丁目を判定します。候補数・物件数・ページ数による打ち切りは行いません。通信失敗やページ送りの異常は未完了として表示します。掲載サイト側の非公開情報・取得制限や、地名データの欠落は取得できません。')
        st.write('同じ建物位置の月額募集額の中央値を求め、250m区画で集計します。近隣推定は750m以内に3建物位置以上ある場合だけ距離の逆二乗で加重平均します。')
        st.write('検索と保存はサーバーのバックグラウンドで実行し、画面は1秒ごとに進捗を表示します。サーバーの休止・再起動を越えて実行することはできません。募集終了物件の自動削除は行いません。')
        st.caption('地名取得の代替経路：Geolonia Japanese Addresses v2（デジタル庁アドレス・ベース・レジストリ由来、CC BY 4.0）。町代表点を使って近隣自治体の全町丁目を検索します。地名データの欠落や境界の完全な網羅は保証できません。')
        st.link_button('町丁目データの出典・ライセンス','https://github.com/geolonia/japanese-addresses-v2')


if __name__=='__main__':
    main()
