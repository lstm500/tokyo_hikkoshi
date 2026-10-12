# VERSION: CLEAN-V68-CARRY-BORROW-SPLIT-2026-10-12

import json

import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client


st.set_page_config(
    page_title="そろばん",
    page_icon="🧮",
    layout="wide",
)

APP_VERSION = "CLEAN-V65-RUNTIME-NAV-FIX"
BUCKET_NAME = "music"
SIGNED_URL_EXPIRES_IN = 3600

BGM_FILES = [
    "1.mp3", "2.mp3", "3.mp3",
    "4.mp3", "5.mp3", "6.mp3",
    "7.mp3", "8.mp3", "9.mp3",
    "10-1.mp3", "10-2.mp3", "10-3.mp3",
]



@st.cache_resource
def get_supabase():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SERVICE_ROLE_KEY"],
    )


@st.cache_data(ttl=3000, show_spinner=False)
def get_bgm_urls():
    bucket = get_supabase().storage.from_(BUCKET_NAME)
    urls = {}

    for filename in BGM_FILES:
        result = bucket.create_signed_url(filename, SIGNED_URL_EXPIRES_IN)

        if isinstance(result, dict):
            signed_url = (
                result.get("signedURL")
                or result.get("signedUrl")
                or result.get("signed_url")
            )
        else:
            signed_url = (
                getattr(result, "signedURL", None)
                or getattr(result, "signedUrl", None)
                or getattr(result, "signed_url", None)
            )

        if not signed_url:
            raise RuntimeError(f"{filename} のSigned URLを取得できませんでした。")

        urls[filename] = signed_url

    return urls


st.caption(f"APP VERSION: {APP_VERSION}")


try:
    BGM_URLS = get_bgm_urls()
except KeyError:
    st.error(
        "Streamlit Secrets に SUPABASE_URL と "
        "SUPABASE_SERVICE_ROLE_KEY を設定してください。"
    )
    st.stop()
except Exception as exc:
    st.error("Supabase Storage のBGMを取得できませんでした。")
    st.caption(str(exc))
    st.stop()


BGM_JSON = json.dumps(BGM_URLS, ensure_ascii=False)
VERSION_JSON = json.dumps(APP_VERSION)

HTML = r"""
<div id="soroban-app">
<style>
  * { box-sizing: border-box; }

  body {
    margin: 0;
    background: #f7f5ef;
    color: #24231f;
    font-family: system-ui, -apple-system, "Hiragino Sans", "Yu Gothic", sans-serif;
  }

  #soroban-app {
    max-width: 1180px;
    margin: 0 auto;
    padding: 16px;
  }

  button, input { font: inherit; }

  button {
    min-height: 46px;
    border: 0;
    border-radius: 12px;
    padding: 10px 16px;
    cursor: pointer;
    font-weight: 800;
    color: #24231f;
    background: #e9e4da;
    touch-action: manipulation;
  }

  button.primary {
    background: #315f86;
    color: #fff;
  }

  button:disabled {
    opacity: .45;
    cursor: not-allowed;
  }

  .version {
    font-size: 11px;
    color: #8a857c;
    margin-bottom: 8px;
  }

  /* =====================================================
     APP TOP
     ===================================================== */
  .app-home {
    padding: 22px 0 12px;
  }

  .home-title {
    margin: 6px 0 8px;
    text-align: center;
    font-size: clamp(26px, 5vw, 42px);
    font-weight: 1000;
    letter-spacing: .02em;
  }

  .home-sub {
    margin: 0 auto 22px;
    max-width: 760px;
    text-align: center;
    color: #6d675f;
    font-size: 14px;
    line-height: 1.7;
  }

  .home-mode-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    margin-top: 6px;
  }


  .home-rank-card {
    width: min(680px, 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 14px;
    margin: 0 auto 34px;
    padding: 12px 16px;
    border: 1px solid #d9cfb8;
    border-radius: 18px;
    background: linear-gradient(135deg, #fffdf7, #f4eddc);
    box-shadow: 0 8px 22px rgba(74, 60, 35, .08);
  }

  .home-rank-copy {
    min-width: 0;
  }

  .home-rank-label {
    color: #786d5a;
    font-size: 10px;
    font-weight: 900;
    letter-spacing: .08em;
  }

  .home-rank-name {
    margin-top: 2px;
    font-size: 22px;
    font-weight: 1000;
  }

  .rank-medal {
    --medal-face: #b77a4e;
    --medal-edge: #795036;
    --medal-text: #34251b;
    --ribbon-left: #365f87;
    --ribbon-right: #9b4c4d;

    position: relative;
    isolation: isolate;
    overflow: visible;
    width: 62px;
    height: 62px;
    flex: 0 0 62px;
    display: grid;
    place-items: center;
    border: 3px solid var(--medal-edge);
    border-radius: 50%;
    background: var(--medal-face);
    color: var(--medal-text);
    box-shadow: 0 3px 0 rgba(55, 45, 34, .14);
    text-align: center;
    font-weight: 1000;
    line-height: 1;
  }

  .rank-medal::before,
  .rank-medal::after {
    content: "";
    position: absolute;
    z-index: -1;
    top: 45px;
    width: 23px;
    height: 42px;
    clip-path: polygon(0 0, 100% 0, 100% 76%, 74% 100%, 50% 83%, 26% 100%, 0 76%);
  }

  .rank-medal::before {
    left: 6px;
    background: var(--ribbon-left);
    transform: rotate(7deg);
    transform-origin: top center;
  }

  .rank-medal::after {
    right: 6px;
    background: var(--ribbon-right);
    transform: rotate(-7deg);
    transform-origin: top center;
  }

  .rank-medal-text {
    position: relative;
    z-index: 2;
    max-width: 52px;
    font-size: 12px;
    text-shadow: none;
  }

  .rank-medal-text::before {
    content: "";
    position: absolute;
    inset: -13px -11px;
    z-index: -1;
    border: 1px solid currentColor;
    border-radius: 50%;
    opacity: .28;
  }

  .rank-medal.no-rank {
    --medal-face: #d8d3ca;
    --medal-edge: #aaa297;
    --medal-text: #6f685f;
    box-shadow: 0 2px 0 rgba(55, 45, 34, .10);
  }

  .rank-medal.no-rank::before,
  .rank-medal.no-rank::after {
    display: none;
  }

  .rank-medal.rank-kyu-low {
    --medal-face: #b77a4e;
    --medal-edge: #795036;
    --medal-text: #34251b;
  }

  .rank-medal.rank-kyu-mid {
    --medal-face: #bdc3c8;
    --medal-edge: #78818a;
    --medal-text: #2f353a;
  }

  .rank-medal.rank-kyu-high {
    --medal-face: #d2aa3c;
    --medal-edge: #8d6d1d;
    --medal-text: #3b3014;
  }

  .rank-medal.rank-dan-1 {
    --medal-face: #b96b43;
    --medal-edge: #7c462d;
    --medal-text: #342116;
  }

  .rank-medal.rank-dan-2 {
    --medal-face: #d4d8db;
    --medal-edge: #7e8790;
    --medal-text: #30363b;
  }

  .rank-medal.rank-dan-3 {
    --medal-face: #d4a92f;
    --medal-edge: #876817;
    --medal-text: #392e11;
  }

  .rank-medal.rank-master {
    --medal-face: #315d80;
    --medal-edge: #c8a044;
    --medal-text: #ffffff;
    --ribbon-left: #243e63;
    --ribbon-right: #8f3d46;
    box-shadow: 0 3px 0 rgba(38, 51, 67, .18);
  }

  .home-mode-btn {
    min-height: 168px;
    padding: 22px 18px;
    border: 2px solid #d6cfc2;
    border-radius: 22px;
    background: #fff;
    text-align: left;
    box-shadow: 0 8px 24px rgba(60, 50, 40, .07);
  }

  .home-mode-btn.primary-home {
    background: #315f86;
    color: #fff;
    border-color: #315f86;
  }

  .home-mode-kicker {
    display: block;
    margin-bottom: 10px;
    font-size: 11px;
    font-weight: 950;
    letter-spacing: .1em;
    opacity: .72;
  }

  .home-mode-name {
    display: block;
    margin-bottom: 9px;
    font-size: clamp(22px, 4vw, 32px);
    font-weight: 1000;
  }

  .home-mode-desc {
    display: block;
    font-size: 13px;
    line-height: 1.6;
    opacity: .86;
  }

  .home-mode-btn.anzan-home {
    background: #fff8e8;
    border-color: #e2d2a9;
  }

  .home-mode-btn.settings-home {
    background: #f4f1ec;
    border-color: #d8d1c6;
  }

  /* =====================================================
     MENTAL ARITHMETIC MODE
     ===================================================== */
  .anzan-view,
  .settings-view {
    display: none;
  }

  .anzan-view.show,
  .settings-view.show {
    display: block;
  }

  .anzan-hero,
  .settings-hero {
    padding: 18px;
    border: 1px solid #dcd5c8;
    border-radius: 20px;
    background: #fff;
  }

  .anzan-hero h2,
  .settings-hero h2 {
    margin: 0 0 7px;
    font-size: clamp(24px, 4vw, 34px);
  }

  .anzan-hero p,
  .settings-hero p {
    margin: 0;
    color: #6b655d;
    font-size: 13px;
    line-height: 1.7;
  }

  .anzan-level-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    margin-top: 16px;
  }

  .anzan-level-card {
    display: flex;
    flex-direction: column;
    min-height: 190px;
    padding: 16px;
    border: 1px solid #ddd7ca;
    border-radius: 18px;
    background: #fff;
  }

  .anzan-level-no {
    color: #8a8277;
    font-size: 11px;
    font-weight: 950;
    letter-spacing: .08em;
  }

  .anzan-level-name {
    margin: 7px 0;
    font-size: 19px;
    font-weight: 1000;
  }

  .anzan-level-desc {
    flex: 1;
    color: #6d675f;
    font-size: 12px;
    line-height: 1.6;
  }

  .anzan-game-card {
    margin-top: 14px;
    padding: 18px;
    border: 2px solid #ddd7ca;
    border-radius: 22px;
    background: #fff;
    text-align: center;
  }

  .anzan-status {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    color: #6e685f;
    font-size: 12px;
    font-weight: 900;
  }

  .anzan-flash {
    min-height: 160px;
    display: grid;
    place-items: center;
    margin: 16px 0 10px;
    border: 2px solid #e0d8ca;
    border-radius: 20px;
    background: #fffdf8;
    font-size: clamp(54px, 12vw, 92px);
    font-weight: 1000;
    font-variant-numeric: tabular-nums;
  }

  .anzan-flash.ready {
    color: #786f65;
    font-size: clamp(28px, 6vw, 42px);
  }

  .anzan-flash.answer {
    border-color: #9db5c8;
    background: #f4f9fc;
  }

  .anzan-hint {
    min-height: 24px;
    margin-bottom: 10px;
    color: #716a61;
    font-size: 12px;
    font-weight: 800;
  }

  .anzan-answer-display {
    width: min(320px, 100%);
    min-height: 64px;
    display: grid;
    place-items: center;
    margin: 10px auto;
    border: 2px solid #cec6b8;
    border-radius: 14px;
    background: #fff;
    font-size: 30px;
    font-weight: 1000;
  }

  .anzan-keypad {
    width: min(430px, 100%);
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin: 10px auto;
  }

  .anzan-keypad button {
    min-height: 58px;
    font-size: 21px;
  }

  .anzan-feedback {
    min-height: 34px;
    margin-top: 10px;
    font-size: 18px;
    font-weight: 950;
  }

  .anzan-feedback.good {
    color: #2f6a43;
  }

  .anzan-feedback.bad {
    color: #a14428;
  }

  .anzan-result {
    margin-top: 14px;
    padding: 24px 18px;
    border: 2px solid #ddd7ca;
    border-radius: 22px;
    background: #fff;
    text-align: center;
  }

  .anzan-result-score {
    margin: 8px 0;
    font-size: clamp(36px, 7vw, 58px);
    font-weight: 1000;
  }

  .anzan-actions {
    display: flex;
    gap: 8px;
    justify-content: center;
    margin-top: 14px;
  }

  /* =====================================================
     SETTINGS
     ===================================================== */
  .settings-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin-top: 16px;
  }

  .setting-card {
    padding: 16px;
    border: 1px solid #ddd7ca;
    border-radius: 18px;
    background: #fff;
  }

  .setting-title {
    margin-bottom: 5px;
    font-size: 16px;
    font-weight: 1000;
  }

  .setting-desc {
    min-height: 38px;
    color: #706960;
    font-size: 12px;
    line-height: 1.55;
  }

  .setting-value {
    margin: 10px 0 7px;
    color: #315f86;
    font-size: 20px;
    font-weight: 1000;
  }

  .setting-card button {
    width: 100%;
  }

  .settings-save-note {
    margin-top: 14px;
    padding: 12px 14px;
    border-radius: 14px;
    background: #f1eee7;
    color: #665f56;
    font-size: 12px;
    line-height: 1.65;
  }

  .subview-topbar {
    position: sticky;
    top: 0;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 0 0 14px;
    padding: 8px 0;
    background: rgba(247,245,239,.96);
    backdrop-filter: blur(6px);
  }

  .subview-topbar-title {
    font-size: 14px;
    font-weight: 950;
    color: #5f5951;
  }

  .top-back-btn {
    position: relative;
    z-index: 1001;
    min-width: 96px;
    min-height: 44px;
    padding: 9px 13px;
    font-size: 13px;
    background: #e8e1d5;
    border: 1px solid #cfc5b7;
    pointer-events: auto !important;
    touch-action: manipulation;
  }

  /* =====================================================
     LEARNING MODE
     ===================================================== */
  .learning-view {
    display: none;
  }

  .learning-view.show {
    display: block;
  }

  .learning-hero {
    padding: 18px 18px 16px;
    border: 1px solid #dcd5c8;
    border-radius: 20px;
    background: #fff;
  }

  .learning-hero h2 {
    margin: 0 0 6px;
    font-size: clamp(24px, 4vw, 34px);
  }

  .learning-hero p {
    margin: 0;
    color: #6b655d;
    line-height: 1.7;
    font-size: 13px;
  }

  .learning-progress-summary {
    margin-top: 12px;
    font-size: 12px;
    font-weight: 850;
    color: #5d574f;
  }

  .learning-progress-track {
    height: 10px;
    margin-top: 7px;
    overflow: hidden;
    border-radius: 999px;
    background: #ebe6dd;
  }

  .learning-progress-bar {
    width: 0%;
    height: 100%;
    border-radius: inherit;
    background: #5d8c62;
    transition: width .3s ease;
  }

  .learning-category-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    margin-top: 16px;
  }

  .learning-category-card {
    display: flex;
    flex-direction: column;
    min-height: 208px;
    padding: 18px;
    border: 1px solid #ddd7ca;
    border-radius: 20px;
    background: #fff;
  }

  .learning-category-symbol {
    font-size: 34px;
    font-weight: 1000;
    line-height: 1;
  }

  .learning-category-title {
    margin: 10px 0 5px;
    font-size: 21px;
    font-weight: 1000;
  }

  .learning-category-desc {
    flex: 1;
    color: #6b655d;
    font-size: 13px;
    line-height: 1.6;
  }

  .learning-stars {
    margin: 10px 0;
    font-size: 14px;
    font-weight: 950;
    letter-spacing: .06em;
    color: #7e6c35;
  }

  .learning-stage-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin-top: 14px;
  }

  .learning-stage-card {
    position: relative;
    min-height: 154px;
    padding: 16px;
    border: 1px solid #ddd7ca;
    border-radius: 18px;
    background: #fff;
  }

  .learning-stage-card.locked {
    opacity: .53;
    background: #f1eee8;
  }

  .learning-stage-card.cleared {
    border-color: #a8c4a7;
    background: #f7fbf5;
  }

  .learning-stage-no {
    font-size: 11px;
    font-weight: 950;
    letter-spacing: .08em;
    color: #8a8277;
  }

  .learning-stage-title {
    margin: 6px 0 5px;
    font-size: 17px;
    font-weight: 950;
  }

  .learning-stage-desc {
    min-height: 38px;
    color: #6d675f;
    font-size: 12px;
    line-height: 1.55;
  }

  .learning-stage-status {
    margin-top: 8px;
    font-size: 12px;
    font-weight: 900;
  }

  .learning-stage-card button {
    width: 100%;
    margin-top: 10px;
    min-height: 40px;
    padding: 7px 10px;
  }

  .learning-lesson-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
    margin: 4px 0 14px;
  }

  .learning-lesson-kicker {
    font-size: 11px;
    font-weight: 950;
    color: #81796f;
    letter-spacing: .08em;
  }

  .learning-lesson-title {
    margin-top: 4px;
    font-size: clamp(22px, 4vw, 30px);
    font-weight: 1000;
  }

  .learning-example-card {
    margin-top: 12px;
    padding: 16px;
    border: 2px solid #d8d0c2;
    border-radius: 20px;
    background: #fffdf8;
    text-align: center;
  }

  .learning-example-label {
    margin-bottom: 6px;
    color: #81796f;
    font-size: 11px;
    font-weight: 950;
    letter-spacing: .08em;
  }

  .learning-example-problem {
    font-size: clamp(34px, 7vw, 58px);
    font-weight: 1000;
    letter-spacing: .04em;
    color: #24231f;
    line-height: 1.25;
  }

  .learning-example-help {
    margin-top: 7px;
    color: #6d675f;
    font-size: 12px;
    line-height: 1.55;
  }

  .learning-video-card,
  .learning-explain-card,
  .learning-quiz-card {
    margin-top: 12px;
    padding: 16px;
    border: 2px solid #ddd7ca;
    border-radius: 20px;
    background: #fff;
  }

  .learning-video-card {
    transition:
      border-color .35s ease,
      box-shadow .35s ease,
      background .35s ease;
  }

  /* スタート前／ループ先頭は通常の枠色。 */
  .learning-video-card.is-active {
    border-color: #8eabc2;
    background: #fbfdff;
    box-shadow: 0 0 0 2px rgba(91, 132, 166, .10);
  }

  /* 答えの珠の形まで到達したら、もう一段階はっきり変える。 */
  .learning-video-card.is-answer {
    border-color: #78a276;
    background: #fbfef9;
    box-shadow: 0 0 0 3px rgba(78, 132, 76, .13);
  }

  .learning-answer-red {
    color: #c9362b;
    font-weight: 1000;
  }

  .learning-video-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
    font-size: 15px;
    font-weight: 950;
  }

  .learning-video-badge {
    padding: 5px 8px;
    border-radius: 999px;
    background: #e8f0e2;
    color: #355635;
    font-size: 11px;
    font-weight: 900;
  }

  .learning-step-text {
    min-height: 52px;
    margin: 8px 0 10px;
    text-align: center;
    font-size: 16px;
    line-height: 1.6;
    font-weight: 900;
    color: #4b463f;
  }

  .learning-frame-counter {
    text-align: center;
    font-size: 11px;
    color: #847c72;
  }

  .learning-video-controls {
    display: flex;
    justify-content: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 10px;
  }

  .learning-video-controls button {
    min-height: 38px;
    padding: 7px 11px;
    font-size: 12px;
  }

  .learning-voice-btn {
    border: 1px solid #d1cabd;
    background: #eee9df;
  }

  .learning-voice-btn.on {
    border-color: #a9cfb3;
    background: #dff1e3;
    color: #285a36;
  }

  .learning-voice-note {
    min-height: 22px;
    margin-top: 7px;
    text-align: center;
    color: #756e65;
    font-size: 11px;
    font-weight: 750;
  }

  .learning-soroban-wrap {
    overflow-x: auto;
    padding: 4px 2px 8px;
  }

  .learning-soroban-board {
    --rod-width: 64px;
    position: relative;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    gap: 5px;
    min-width: max-content;
    padding: 12px 14px 8px;
    border: 5px solid #73543a;
    border-radius: 14px;
    background: #e8c895;
    box-shadow:
      inset 0 0 0 2px rgba(255,255,255,.35),
      0 6px 15px rgba(72, 54, 37, .12);
  }

  .learning-soroban-board::before {
    content: "";
    position: absolute;
    left: 7px;
    right: 7px;
    top: 78px;
    height: 8px;
    border-radius: 5px;
    background: #66452f;
    z-index: 3;
  }

  .learning-point-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }

  .learning-point {
    padding: 12px;
    border-radius: 14px;
    background: #f7f4ee;
  }

  .learning-point.trouble {
    background: #fff6df;
  }

  .learning-point-label {
    margin-bottom: 5px;
    font-size: 12px;
    font-weight: 950;
  }

  .learning-point-text {
    color: #5f5951;
    font-size: 13px;
    line-height: 1.65;
  }

  .learning-rule-box {
    margin-top: 10px;
    padding: 12px 14px;
    border-left: 5px solid #6f936c;
    border-radius: 10px;
    background: #f3f8f1;
    font-size: 14px;
    font-weight: 900;
    line-height: 1.65;
  }

  .learning-quiz-question {
    margin-bottom: 10px;
    font-size: 16px;
    font-weight: 950;
    line-height: 1.55;
  }

  .learning-quiz-options {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .learning-quiz-option {
    min-height: 54px;
    font-size: 16px;
  }

  .learning-quiz-option.correct-choice {
    background: #dff1e3;
    color: #285a36;
  }

  .learning-quiz-option.wrong-choice {
    background: #f4ded8;
    color: #8a3d28;
  }

  .learning-quiz-feedback {
    min-height: 34px;
    margin-top: 10px;
    text-align: center;
    font-size: 15px;
    font-weight: 950;
  }

  .learning-clear-actions {
    display: none;
    gap: 8px;
    margin-top: 10px;
  }

  .learning-clear-actions.show {
    display: flex;
  }

  .learning-clear-actions button {
    flex: 1;
  }

  @media (max-width: 640px) {
    .home-mode-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .home-mode-btn {
      min-height: 148px;
      padding: 14px 10px;
    }

    .home-mode-name {
      font-size: 20px;
    }

    .home-mode-desc {
      font-size: 11px;
    }

    .learning-category-grid,
    .learning-stage-grid,
    .learning-point-grid,
    .anzan-level-grid,
    .settings-grid {
      grid-template-columns: 1fr;
    }

    .learning-quiz-options {
      grid-template-columns: 1fr;
    }
  }

  .intro {
    display: flex;
    justify-content: space-between;
    align-items: end;
    gap: 16px;
    margin-bottom: 18px;
  }

  .lead {
    max-width: 820px;
    font-size: 14px;
    line-height: 1.7;
    color: #66635d;
  }

  .time-limit-control {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    flex-wrap: wrap;
    margin: 10px 0 20px;
  }

  .time-limit-label {
    color: #66635d;
    font-size: 13px;
    font-weight: 850;
  }

  .time-limit-toggle {
    min-height: 42px;
    padding: 8px 14px;
    border-radius: 999px;
    border: 1px solid #cfc7b9;
    background: #fff;
    color: #2e2b27;
    font-size: 13px;
    font-weight: 900;
  }

  .time-limit-toggle.off {
    background: #ece8df;
    color: #56504a;
  }

  #soroban-app.untimed-mode .ranking-open-btn {
    display: none;
  }

  .badge {
    white-space: nowrap;
    border: 1px solid #d8d2c6;
    border-radius: 999px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 850;
  }

  .level-title {
    margin: 22px 0 10px;
    font-size: 20px;
    font-weight: 900;
  }

  .mode-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
  }

  .mode-card {
    display: flex;
    flex-direction: column;
    min-height: 180px;
    padding: 16px;
    border: 1px solid #ddd7ca;
    border-radius: 18px;
    background: #fff;
  }

  .mode-no {
    font-size: 12px;
    font-weight: 900;
    letter-spacing: .08em;
    color: #817b70;
  }

  .mode-name {
    margin: 6px 0 8px;
    font-size: 18px;
    font-weight: 900;
  }

  .mode-desc {
    flex: 1;
    font-size: 13px;
    line-height: 1.6;
    color: #66635d;
  }

  .chips {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin: 10px 0;
  }

  .chip {
    padding: 5px 8px;
    border-radius: 999px;
    background: #f2eee5;
    font-size: 12px;
    font-weight: 800;
  }

  .hidden { display: none !important; }
  .workspace { display: none; }
  .workspace.show { display: block; }

  .statusbar {
    position: sticky;
    top: 0;
    z-index: 35;
    display: grid;
    grid-template-columns: minmax(200px, 1fr) auto auto auto auto auto auto;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    border: 1px solid #ddd7ca;
    border-radius: 18px;
    background: #fff;
  }

  .status-title {
    font-size: 16px;
    font-weight: 900;
  }

  .status-sub {
    margin-top: 3px;
    font-size: 12px;
    color: #706c64;
  }

  .timer {
    min-width: 88px;
    text-align: center;
    font-size: 25px;
    font-weight: 950;
    font-variant-numeric: tabular-nums;
  }

  .timer.warn { color: #a14428; }

  .streak {
    white-space: nowrap;
    font-size: 14px;
    font-weight: 900;
  }

  .voice-btn.on {
    background: #dff1e3;
    color: #285a36;
    border: 1px solid #a9cfb3;
  }

  .challenge-quick-nav {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .challenge-quick-nav button {
    min-height: 40px;
    font-size: 12px;
  }

  .voice-status {
    grid-column: 1 / -1;
    font-size: 12px;
    color: #706c64;
  }

  .voice-status.listening {
    color: #2f6a43;
    font-weight: 900;
  }

  .bgm-name {
    grid-column: 1 / -1;
    font-size: 12px;
    color: #706c64;
  }

  .question-card.voice-waiting .qtop,
  .question-card.voice-waiting .equation,
  .question-card.voice-waiting .hint-launch,
  .question-card.voice-waiting .abacus-hint,
  .question-card.voice-waiting .answer-form,
  .question-card.voice-waiting .keypad,
  .question-card.voice-waiting .feedback {
    visibility: hidden !important;
  }

  .question-card.voice-waiting::after {
    content: "ご破算！";
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    font-size: clamp(34px, 7vw, 64px);
    font-weight: 1000;
    letter-spacing: .08em;
    color: #5c5144;
    pointer-events: none;
  }

  .question-card {
    position: relative;
    min-height: 560px;
    margin-top: 14px;
    padding: 24px;
    border: 2px solid #ddd7ca;
    border-radius: 22px;
    background: #fff;
    transition:
      border-color .25s ease,
      border-width .25s ease,
      box-shadow .25s ease,
      background .25s ease,
      transform .25s ease;
  }

  .question-card.streak6,
  .question-card.streak10 {
    border: 7px solid transparent !important;
    background:
      linear-gradient(#fffdf9, #fffdf9) padding-box,
      linear-gradient(
        90deg,
        var(--rb1, #ff3b30),
        var(--rb2, #ff9500),
        var(--rb3, #ffd60a),
        var(--rb4, #34c759),
        var(--rb5, #0a84ff),
        var(--rb6, #5e5ce6),
        var(--rb7, #bf5af2),
        var(--rb8, #ff3b30)
      ) border-box !important;
    background-size: 100% 100%, 300% 100% !important;
    animation: rainbowBorder 2.2s linear infinite;
  }

  .question-card.streak6 {
    box-shadow: none !important;
  }

  .question-card.streak10 {
    box-shadow: none;
  }

  /* 10問連続正解以上：
     レインボー枠は常時、発光は新しい問題が出た瞬間だけ。 */
  .question-card.streak10.question-flash {
    animation:
      rainbowBorder 2.2s linear infinite;
  }

  .question-card.streak10.question-flash::before {
    content: "";
    position: absolute;
    inset: -14px;
    border-radius: 32px;
    pointer-events: none;
    z-index: 20;
    opacity: 0;
    border: 6px solid rgba(255,255,255,.94);
    box-shadow:
      0 0 18px 7px rgba(255, 214, 10, .75),
      0 0 38px 15px rgba(255, 59, 48, .58),
      0 0 62px 22px rgba(10, 132, 255, .46),
      inset 0 0 22px rgba(191, 90, 242, .42);
    animation:
      questionFlashHalo 1.15s cubic-bezier(.16,.8,.25,1);
  }

  @keyframes rainbowBorder {
    from { background-position: 0 0, 0% 50%; }
    to   { background-position: 0 0, 300% 50%; }
  }

  @keyframes questionFlashHalo {
    0% {
      opacity: 0;
      transform: scale(.975);
    }
    16% {
      opacity: 1;
      transform: scale(1.015);
    }
    42% {
      opacity: .95;
      transform: scale(1.025);
    }
    100% {
      opacity: 0;
      transform: scale(1.045);
    }
  }

  .qtop {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }

  .qcount, .score {
    font-size: 13px;
    font-weight: 850;
  }

  .qcount { color: #706c64; }

  .weak-badge {
    display: none;
    margin-left: 8px;
    padding: 5px 9px;
    border-radius: 999px;
    background: #fff1c9;
    color: #805b00;
    font-size: 12px;
    font-weight: 900;
  }

  .weak-badge.show {
    display: inline-block;
  }

  .equation {
    margin: 38px 0 22px;
    text-align: center;
    font-size: clamp(42px, 7vw, 68px);
    font-weight: 950;
    letter-spacing: .03em;
  }

  .hint-launch {
    display: flex;
    justify-content: center;
    margin: -4px 0 14px;
  }

  .hint-btn {
    background: #e8f0e2;
    color: #345233;
    border: 1px solid #c9d8c0;
  }

  .abacus-hint {
    display: none;
    width: min(760px, 100%);
    margin: 0 auto 18px;
    padding: 14px;
    border: 2px solid #d7c69e;
    border-radius: 18px;
    background: #fffaf0;
  }

  .abacus-hint.show {
    display: block;
    animation: hintOpen .22s ease-out;
  }

  @keyframes hintOpen {
    from {
      opacity: 0;
      transform: translateY(-6px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .hint-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }

  .hint-title {
    font-size: 15px;
    font-weight: 950;
  }

  .hint-manual-nav {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 8px;
    margin: 12px 0 8px;
  }

  .hint-manual-nav button {
    min-height: 46px;
    font-size: 14px;
    font-weight: 950;
  }

  .hint-manual-counter {
    min-width: 68px;
    text-align: center;
    color: #6d665d;
    font-size: 12px;
    font-weight: 950;
  }

  .hint-manual-nav button:disabled {
    opacity: .35;
    cursor: default;
  }

  .hint-step {
    min-height: 42px;
    margin: 8px 0 10px;
    text-align: center;
    font-size: 16px;
    font-weight: 900;
    line-height: 1.55;
    color: #4c473e;
  }

  .hint-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }

  .hint-actions button {
    min-height: 36px;
    padding: 7px 10px;
    font-size: 12px;
  }

  .soroban-scroll {
    overflow-x: auto;
    padding: 4px 2px 8px;
  }

  .soroban-board {
    --rod-width: 64px;
    position: relative;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    gap: 5px;
    min-width: max-content;
    padding: 12px 14px 8px;
    border: 5px solid #73543a;
    border-radius: 14px;
    background: #e8c895;
    box-shadow:
      inset 0 0 0 2px rgba(255,255,255,.35),
      0 6px 15px rgba(72, 54, 37, .12);
  }

  .soroban-board::before {
    content: "";
    position: absolute;
    left: 7px;
    right: 7px;
    top: 78px;
    height: 8px;
    border-radius: 5px;
    background: #66452f;
    z-index: 3;
  }

  .soroban-rod {
    position: relative;
    width: var(--rod-width);
    height: 202px;
    border-radius: 12px;
    transition:
      background .25s ease,
      box-shadow .25s ease;
  }

  .soroban-rod.active {
    background: rgba(255, 238, 155, .58);
    box-shadow:
      inset 0 0 0 2px rgba(236, 175, 37, .78),
      0 0 16px rgba(236, 175, 37, .38);
  }

  .rod-line {
    position: absolute;
    left: 50%;
    top: 5px;
    bottom: 30px;
    width: 3px;
    transform: translateX(-50%);
    border-radius: 2px;
    background: #6b4b33;
    z-index: 1;
  }

  .soroban-bead {
    position: absolute;
    left: 50%;
    width: 48px;
    height: 16px;
    transform: translateX(-50%);
    border: 2px solid #8d4929;
    border-radius: 50% 50% 44% 44%;
    background:
      linear-gradient(
        180deg,
        #ef9b5c 0%,
        #c86735 54%,
        #a84d27 100%
      );
    box-shadow:
      inset 0 2px 2px rgba(255,255,255,.38),
      0 2px 3px rgba(77,40,21,.24);
    z-index: 4;
    transition:
      top .58s cubic-bezier(.22,.8,.24,1),
      filter .22s ease,
      transform .22s ease;
  }

  .soroban-rod.active .soroban-bead {
    filter: saturate(1.14) brightness(1.04);
  }

  .upper-bead {
    top: 13px;
  }

  .lower-bead {
    top: 112px;
  }

  .rod-place {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 5px;
    text-align: center;
    font-size: 12px;
    font-weight: 950;
    color: #604a39;
  }

  .hint-legend {
    margin-top: 8px;
    text-align: center;
    font-size: 11px;
    line-height: 1.5;
    color: #71695f;
  }

  .answer-form {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .answer-input {
    width: min(280px, 100%);
    min-height: 62px;
    padding: 8px 12px;
    border: 2px solid #c8c1b4;
    border-radius: 14px;
    background: #fff;
    color: #24231f;
    text-align: center;
    font-size: 30px;
    font-weight: 900;
  }

  .keypad-wrap {
    width: min(360px, 100%);
    margin: 16px auto 0;
  }

  .keypad-label {
    margin-bottom: 8px;
    text-align: center;
    font-size: 13px;
    font-weight: 850;
    color: #706c64;
  }

  .keypad {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 9px;
  }

  .keypad button {
    min-height: 62px;
    border: 1px solid #d4cec1;
    background: #f3efe7;
    color: #24231f;
    font-size: 24px;
    font-weight: 900;
    user-select: none;
  }

  .keypad button:active {
    transform: translateY(1px);
    background: #e7e0d4;
  }

  .keypad .key-action {
    background: #e5e0d6;
    font-size: 14px;
  }

  .feedback {
    min-height: 36px;
    margin-top: 14px;
    text-align: center;
    font-size: 20px;
    font-weight: 900;
  }

  .feedback.good { color: #28704a; }
  .feedback.bad { color: #a14428; }

  .progress {
    height: 8px;
    margin-top: 18px;
    overflow: hidden;
    border-radius: 999px;
    background: #e7e2d9;
  }

  .bar {
    width: 0;
    height: 100%;
    background: #6d815d;
    transition: width .25s ease;
  }

  .results {
    display: none;
    margin-top: 14px;
    padding: 24px;
    border: 1px solid #ddd7ca;
    border-radius: 22px;
    background: #fff;
  }

  .results.show { display: block; }

  .score-stage {
    position: relative;
    overflow: hidden;
    margin: 10px auto 14px;
    padding: 22px 12px 18px;
    border: 2px solid #e1d6bf;
    border-radius: 22px;
    background:
      radial-gradient(circle at 50% 20%, #fff8d4 0, #fff 56%);
    text-align: center;
  }

  .score-caption {
    margin-bottom: 4px;
    font-size: 14px;
    font-weight: 900;
    color: #74654e;
  }

  .result-score {
    min-height: 74px;
    margin: 6px 0;
    text-align: center;
    font-size: clamp(54px, 10vw, 88px);
    line-height: 1;
    font-weight: 1000;
    letter-spacing: .02em;
    font-variant-numeric: tabular-nums;
    transform-origin: center;
  }

  .result-score.roulette {
    animation: scoreRoulette .16s linear infinite;
    filter: blur(.35px);
  }

  .result-score.reveal {
    animation: scoreReveal .85s cubic-bezier(.18,.9,.2,1.25);
    text-shadow:
      0 3px 0 rgba(255,255,255,.9),
      0 0 18px rgba(255, 189, 35, .55),
      0 0 34px rgba(255, 96, 52, .34);
  }

  @keyframes scoreRoulette {
    0%   { transform: perspective(500px) rotateX(0deg) scale(1); }
    50%  { transform: perspective(500px) rotateX(90deg) scale(1.04); }
    100% { transform: perspective(500px) rotateX(180deg) scale(1); }
  }

  @keyframes scoreReveal {
    0%   { transform: scale(.35) rotate(-7deg); opacity: .2; }
    55%  { transform: scale(1.32) rotate(3deg); opacity: 1; }
    75%  { transform: scale(.94) rotate(-1deg); }
    100% { transform: scale(1) rotate(0); }
  }

  .score-formula {
    min-height: 44px;
    margin-top: 8px;
    font-size: 13px;
    line-height: 1.65;
    color: #685f55;
  }

  .score-stage.score-rainbow {
    border: 7px solid transparent;
    background:
      radial-gradient(circle at 50% 20%, #fff8d4 0, #fff 56%) padding-box,
      linear-gradient(
        90deg,
        #ff3b30,
        #ff9500,
        #ffd60a,
        #34c759,
        #0a84ff,
        #5e5ce6,
        #bf5af2,
        #ff3b30
      ) border-box;
    background-size: 100% 100%, 300% 100%;
    animation: scoreRainbow 2s linear infinite;
  }

  .score-stage.babaan {
    animation: scoreStageBang .72s ease-out;
  }

  .score-stage.score-rainbow.babaan {
    animation:
      scoreStageBang .72s ease-out,
      scoreRainbow 2s linear infinite;
  }

  @keyframes scoreRainbow {
    from { background-position: 0 0, 0% 50%; }
    to   { background-position: 0 0, 300% 50%; }
  }

  @keyframes scoreStageBang {
    0%   { transform: scale(1); }
    24%  { transform: scale(1.035); }
    45%  { transform: scale(.985); }
    100% { transform: scale(1); }
  }

  .confetti-piece {
    position: absolute;
    top: -16px;
    width: 9px;
    height: 16px;
    border-radius: 2px;
    pointer-events: none;
    z-index: 8;
    animation: confettiFall 1.8s ease-in forwards;
  }

  @keyframes confettiFall {
    0% {
      transform: translate3d(0, -10px, 0) rotate(0deg);
      opacity: 1;
    }
    100% {
      transform: translate3d(var(--drift), 240px, 0) rotate(var(--spin));
      opacity: 0;
    }
  }

  .result-note {
    text-align: center;
    color: #66635d;
    line-height: 1.7;
  }

  .ranking-panel {
    margin: 18px auto;
    padding: 14px;
    border: 1px solid #ddd7ca;
    border-radius: 16px;
    background: #fbfaf7;
  }

  .ranking-panel-title {
    margin-bottom: 8px;
    font-size: 14px;
    font-weight: 950;
    color: #554d43;
  }

  .ranking-note {
    margin-bottom: 8px;
    font-size: 10px;
    line-height: 1.5;
    color: #857d73;
  }

  .ranking-list {
    display: grid;
    gap: 5px;
    max-height: 330px;
    overflow-y: auto;
    padding-right: 2px;
  }

  .ranking-empty {
    padding: 12px 6px;
    text-align: center;
    font-size: 11px;
    color: #928b82;
  }

  .ranking-row {
    display: grid;
    grid-template-columns: 25px minmax(0, 1fr) auto;
    align-items: center;
    gap: 7px;
    padding: 7px 8px;
    border: 1px solid #e7e1d7;
    border-radius: 10px;
    background: #fff;
  }

  .ranking-place {
    text-align: center;
    font-size: 10px;
    font-weight: 900;
    color: #8b8174;
  }

  .ranking-main {
    min-width: 0;
  }

  /* ランキング内の点数は意図的に小さく表示 */
  .ranking-score {
    font-size: 17px;
    line-height: 1.15;
    font-weight: 950;
    font-variant-numeric: tabular-nums;
    color: #463f37;
  }

  .ranking-detail {
    margin-top: 2px;
    font-size: 9px;
    line-height: 1.45;
    color: #91887d;
  }

  .ranking-delete {
    min-height: 28px !important;
    padding: 4px 7px !important;
    font-size: 9px !important;
    color: #765d59;
    background: #fff;
    border: 1px solid #d9c9c6;
  }

  .mode-challenge-count {
    margin: 8px 0 5px;
    text-align: right;
    font-size: 10px;
    font-weight: 800;
    color: #8b8378;
  }

  .ranking-open-btn {
    width: 100%;
    margin-top: 7px;
    min-height: 34px;
    padding: 6px 10px;
    font-size: 11px;
    color: #62594f;
    background: #f7f4ee;
    border: 1px solid #ddd5c8;
  }

  .analysis-control {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    margin: 10px 0 18px;
    padding: 14px 16px;
    border: 1px solid #d9d1c4;
    border-radius: 16px;
    background: #fffdf8;
  }

  .analysis-control-title {
    font-size: 14px;
    font-weight: 950;
  }

  .analysis-control-note {
    margin-top: 3px;
    color: #706960;
    font-size: 11px;
    line-height: 1.55;
  }

  .analysis-toggle.on {
    background: #dff1e3;
    color: #285a36;
    border-color: #a9cfb3;
  }

  .recording-badge {
    display: none;
    align-items: center;
    justify-content: center;
    min-height: 34px;
    padding: 6px 10px;
    border-radius: 999px;
    background: #f7dfda;
    color: #9b3524;
    font-size: 11px;
    font-weight: 1000;
    white-space: nowrap;
  }

  .recording-badge.show {
    display: inline-flex;
  }

  .mode-card.camera-setup-active > .start-mode {
    display: none;
  }

  .mode-card.camera-setup-active {
    border-color: #b9c9d5;
  }

  .camera-modal {
    display: none;
    position: static;
    width: 100%;
    margin-top: 12px;
    padding: 0;
    background: transparent;
  }

  .camera-modal.show {
    display: block;
  }

  .camera-dialog {
    width: 100%;
    max-height: none;
    overflow: visible;
    padding: 14px;
    border: 2px solid #d8d0c3;
    border-radius: 18px;
    background: #fffdf8;
    box-shadow: 0 8px 22px rgba(0,0,0,.10);
  }

  .camera-dialog h3 {
    margin: 0 0 6px;
    font-size: 20px;
  }

  .camera-dialog p {
    margin: 0 0 12px;
    color: #675f56;
    font-size: 12px;
    line-height: 1.65;
  }

  .camera-preview-wrap {
    position: relative;
    width: min(100%, 420px);
    aspect-ratio: 4 / 3;
    margin: 0 auto;
    overflow: hidden;
    border-radius: 14px;
    background: #171717;
  }

  .camera-preview {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center 62%;
    background: #171717;
    transform: scaleX(-1);
  }

  .camera-guide {
    position: absolute;
    left: 5%;
    right: 5%;
    bottom: 5%;
    height: 50%;
    border: 3px dashed rgba(255,255,255,.92);
    border-radius: 16px;
    background: rgba(255,255,255,.04);
    pointer-events: none;
  }

  .camera-guide-label {
    position: absolute;
    left: 50%;
    bottom: 8px;
    transform: translateX(-50%);
    padding: 5px 8px;
    border-radius: 999px;
    background: rgba(0,0,0,.66);
    color: #fff;
    font-size: 10px;
    font-weight: 900;
    white-space: nowrap;
  }

  .camera-face-guide {
    position: absolute;
    left: 50%;
    top: 7%;
    transform: translateX(-50%);
    width: 28%;
    height: 28%;
    border: 2px dashed rgba(255,255,255,.52);
    border-radius: 48%;
    pointer-events: none;
  }

  .camera-face-label {
    position: absolute;
    left: 50%;
    top: 7px;
    transform: translateX(-50%);
    padding: 4px 7px;
    border-radius: 999px;
    background: rgba(0,0,0,.52);
    color: #fff;
    font-size: 9px;
    font-weight: 850;
    white-space: nowrap;
  }

  .camera-status {
    margin-top: 10px;
    padding: 10px 12px;
    border-radius: 12px;
    background: #f2eee6;
    color: #5f594f;
    font-size: 12px;
    line-height: 1.6;
  }

  .camera-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-top: 10px;
  }

  .camera-actions button {
    min-height: 42px;
    font-size: 12px;
    font-weight: 950;
  }

  .camera-actions #cameraBeginBtn {
    grid-column: 1 / -1;
    min-height: 48px;
    font-size: 14px;
  }

  .coaching-rank-strip {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-top: 12px;
    padding: 12px 14px;
    border-radius: 14px;
    background: #ece7dc;
  }

  .coaching-rank-label {
    color: #6e665d;
    font-size: 11px;
    font-weight: 850;
  }

  .coaching-rank-title {
    margin-top: 2px;
    font-size: 24px;
    font-weight: 1000;
  }

  .coaching-issues {
    display: grid;
    gap: 10px;
    margin-top: 12px;
  }

  .coaching-issue-card {
    padding: 13px;
    border: 1px solid #d8d0c3;
    border-radius: 15px;
    background: #fff;
  }

  .coaching-issue-title {
    font-size: 16px;
    font-weight: 1000;
  }

  .coaching-issue-evidence {
    margin-top: 4px;
    color: #746c62;
    font-size: 11px;
    line-height: 1.55;
  }

  .coaching-issue-explain {
    margin-top: 8px;
    padding: 10px 11px;
    border-radius: 11px;
    background: #f2eee6;
    color: #514b44;
    font-size: 12px;
    line-height: 1.65;
  }

  .coaching-issue-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-top: 9px;
  }

  .coaching-issue-actions button {
    min-height: 44px;
    font-size: 12px;
    font-weight: 950;
  }

  .app-rank-card {
    margin-top: 16px;
    padding: 16px;
    border: 1px solid #d8d0c3;
    border-radius: 18px;
    background: #fffdf8;
  }

  .app-rank-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
  }

  .app-rank-title {
    font-size: 15px;
    font-weight: 1000;
  }

  .app-rank-badge {
    padding: 7px 11px;
    border-radius: 999px;
    background: #e8dfc5;
    color: #614f1f;
    font-size: 16px;
    font-weight: 1000;
  }

  .app-rank-meter {
    height: 10px;
    margin-top: 12px;
    overflow: hidden;
    border-radius: 999px;
    background: #e7e1d7;
  }

  .app-rank-meter-fill {
    width: 0;
    height: 100%;
    border-radius: inherit;
    background: #7d8c72;
    transition: width .25s ease;
  }

  .app-rank-note,
  .app-rank-next {
    margin-top: 8px;
    color: #6c655c;
    font-size: 11px;
    line-height: 1.6;
  }


  .medal-collection {
    margin-top: 16px;
    padding: 16px;
    border: 1px solid #d8d0c3;
    border-radius: 18px;
    background: #fffdf8;
  }

  .medal-collection-title {
    font-size: 17px;
    font-weight: 1000;
  }

  .medal-collection-note {
    margin-top: 4px;
    color: #71695f;
    font-size: 11px;
    line-height: 1.6;
  }

  .medal-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-top: 14px;
  }

  .medal-slot {
    min-height: 122px;
    display: grid;
    place-items: center;
    align-content: center;
    gap: 8px;
    padding: 10px 6px;
    border: 1px solid #ded7cb;
    border-radius: 15px;
    background: #fff;
    text-align: center;
  }

  .medal-slot.locked {
    background: #f4f1ec;
    color: #9c958b;
  }

  .medal-slot.locked .rank-medal {
    filter: grayscale(1);
    opacity: .22;
  }

  .medal-slot.earned {
    box-shadow: 0 7px 18px rgba(95, 75, 40, .09);
  }

  .medal-slot-name {
    font-size: 11px;
    font-weight: 950;
  }

  .medal-slot-status {
    min-height: 14px;
    color: #837a6d;
    font-size: 9px;
    line-height: 1.35;
  }

  .rank-celebration {
    display: none;
    pointer-events: none;
    position: fixed;
    inset: 0;
    z-index: 5000;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    padding: 18px;
    background:
      radial-gradient(circle at center, rgba(43,58,83,.38), rgba(15,17,24,.88));
  }

  .rank-celebration.show {
    display: flex;
    pointer-events: auto;
    animation: rankOverlayIn .35s ease both;
  }

  .rank-celebration-card {
    position: relative;
    z-index: 3;
    width: min(430px, 94vw);
    padding: 28px 18px 22px;
    border: 2px solid rgba(255,255,255,.7);
    border-radius: 28px;
    background: #fffaf0;
    box-shadow:
      0 24px 80px rgba(0,0,0,.30),
      0 0 0 6px rgba(224,190,93,.18);
    text-align: center;
    animation: rankCardPop .75s cubic-bezier(.18,.89,.32,1.28) both;
  }

  .rank-celebration-kicker {
    color: #86691f;
    font-size: 12px;
    font-weight: 1000;
    letter-spacing: .16em;
  }

  .rank-celebration-medal {
    width: 132px;
    height: 132px;
    margin: 18px auto 22px;
    border-width: 7px;
    animation:
      medalReveal 1s cubic-bezier(.17,.86,.24,1.35) both;
  }

  .rank-celebration-medal::before,
  .rank-celebration-medal::after {
    top: 100px;
    width: 42px;
    height: 72px;
  }

  .rank-celebration-medal::before { left: 18px; }
  .rank-celebration-medal::after { right: 18px; }

  .rank-celebration-medal .rank-medal-text {
    max-width: 100px;
    font-size: 25px;
  }

  .rank-celebration-title {
    font-size: clamp(28px, 8vw, 42px);
    font-weight: 1000;
    letter-spacing: .02em;
  }

  .rank-celebration-sub {
    margin-top: 8px;
    color: #6d624e;
    font-size: 12px;
    line-height: 1.65;
  }

  .rank-celebration button {
    width: min(260px, 100%);
    margin-top: 18px;
    min-height: 50px;
    font-size: 15px;
  }

  .rank-burst {
    position: absolute;
    left: 50%;
    top: 50%;
    width: 520px;
    height: 520px;
    margin: -260px 0 0 -260px;
    border-radius: 50%;
    background:
      repeating-conic-gradient(
        from 0deg,
        rgba(255,231,122,.48) 0 5deg,
        transparent 5deg 15deg
      );
    animation: rankBurstSpin 14s linear infinite;
  }

  .rank-confetti {
    position: absolute;
    z-index: 2;
    top: -12vh;
    width: 9px;
    height: 20px;
    border-radius: 3px;
    opacity: .95;
    animation: confettiFall var(--fall, 2.8s) linear var(--delay, 0s) infinite;
  }

  @keyframes rankOverlayIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  @keyframes rankCardPop {
    0% { transform: scale(.56) translateY(28px); opacity: 0; }
    68% { transform: scale(1.06) translateY(-4px); opacity: 1; }
    100% { transform: scale(1) translateY(0); opacity: 1; }
  }

  @keyframes medalReveal {
    0% { transform: scale(.25) rotate(-24deg); opacity: 0; }
    65% { transform: scale(1.16) rotate(6deg); opacity: 1; }
    100% { transform: scale(1) rotate(0); opacity: 1; }
  }


  @keyframes rankBurstSpin {
    to { transform: rotate(360deg); }
  }

  @keyframes confettiFall {
    0% { transform: translate3d(0, -12vh, 0) rotate(0); }
    100% { transform: translate3d(var(--drift, 30px), 115vh, 0) rotate(720deg); }
  }

  @media (max-width: 760px) {
    .medal-grid {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }

    .home-rank-card {
      justify-content: flex-start;
      margin-bottom: 40px;
    }

    .home-rank-name {
      font-size: 19px;
    }
  }


  .hint-btn.recording-disabled {
    opacity: .65;
    cursor: default;
  }

  .video-review-panel {
    margin-top: 18px;
    padding: 16px;
    border: 1px solid #d9d1c4;
    border-radius: 18px;
    background: #fffdf8;
  }

  .video-review-title {
    font-size: 18px;
    font-weight: 1000;
  }

  .video-review-note {
    margin-top: 4px;
    color: #6f685f;
    font-size: 11px;
    line-height: 1.6;
  }

  .video-review-player {
    display: block;
    width: 100%;
    max-height: 52vh;
    margin-top: 12px;
    border-radius: 14px;
    background: #111;
  }

  .video-review-summary {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
    margin-top: 12px;
  }

  .video-summary-card {
    padding: 10px;
    border-radius: 12px;
    background: #f1ede4;
    text-align: center;
  }

  .video-summary-value {
    font-size: 18px;
    font-weight: 1000;
  }

  .video-summary-label {
    margin-top: 2px;
    color: #726a60;
    font-size: 10px;
    font-weight: 850;
  }

  .video-priority {
    margin-top: 12px;
    padding: 11px 12px;
    border-radius: 12px;
    background: #f6f0df;
    color: #655629;
    font-size: 12px;
    line-height: 1.6;
  }

  .video-question-list {
    display: grid;
    gap: 8px;
    margin-top: 12px;
  }

  .video-question-item {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 10px;
    align-items: center;
    padding: 10px 12px;
    border-radius: 12px;
    background: #f3efe7;
  }

  .video-question-item.wrong {
    border-left: 4px solid #a44b32;
  }

  .video-question-item.correct {
    border-left: 4px solid #437454;
  }

  .video-question-main {
    min-width: 0;
    font-size: 12px;
    line-height: 1.55;
  }

  .video-question-meta {
    color: #756e65;
    font-size: 10px;
  }

  .video-review-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
  }

  @media (max-width: 760px) {
    .analysis-control {
      align-items: flex-start;
      flex-direction: column;
    }

    .video-review-summary {
      grid-template-columns: 1fr;
    }

    .video-question-item {
      grid-template-columns: 1fr;
    }

    .coaching-issue-actions {
      grid-template-columns: 1fr;
    }

    .camera-dialog {
      width: 100%;
      max-height: none;
      padding: 10px;
      border-radius: 16px;
    }

    .camera-dialog h3 {
      margin-bottom: 4px;
      font-size: 17px;
    }

    .camera-dialog p {
      margin-bottom: 7px;
      font-size: 10px;
      line-height: 1.45;
    }

    .camera-preview-wrap {
      width: min(100%, 320px);
      aspect-ratio: 4 / 3;
      margin: 0 auto;
    }

    .camera-preview {
      width: 100%;
      height: 100%;
      object-fit: cover;
      object-position: center 64%;
    }

    .camera-status {
      margin-top: 6px;
      padding: 7px 9px;
      font-size: 10px;
      line-height: 1.45;
    }

    .camera-actions {
      gap: 6px;
      margin-top: 7px;
    }

    .camera-actions button {
      min-height: 38px;
      padding: 7px 6px;
      font-size: 11px;
    }

    .camera-actions #cameraBeginBtn {
      min-height: 44px;
      font-size: 13px;
    }
  }

  .ranking-modal {
    display: none;
    position: fixed;
    inset: 0;
    z-index: 1000;
    padding: 24px;
    background: rgba(38, 34, 30, .38);
    align-items: center;
    justify-content: center;
  }

  .ranking-modal.show {
    display: flex;
  }

  .ranking-dialog {
    width: min(560px, 96vw);
    max-height: 82vh;
    overflow: hidden;
    padding: 18px;
    border-radius: 18px;
    background: #fff;
    box-shadow: 0 20px 60px rgba(0,0,0,.2);
  }

  .ranking-dialog-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
  }

  .ranking-dialog-title {
    min-width: 0;
    font-size: 15px;
    font-weight: 950;
  }

  .ranking-dialog-close {
    min-height: 30px !important;
    padding: 5px 9px !important;
    font-size: 10px !important;
  }

  .ranking-dialog .ranking-list {
    max-height: 60vh;
  }

  .review {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin-top: 18px;
  }

  .review-item {
    padding: 10px 12px;
    border-radius: 12px;
    background: #f3efe7;
    font-size: 13px;
    line-height: 1.5;
  }

  .review-item.correct { border-left: 4px solid #437454; }
  .review-item.wrong { border-left: 4px solid #a44b32; }

  .actions {
    display: flex;
    justify-content: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 18px;
  }

  @media (prefers-reduced-motion: reduce) {
    .question-card.streak10,
    .rank-celebration,
    .rank-celebration-card,
    .rank-celebration-medal,
    .rank-burst,
    .rank-confetti {
      animation: none !important;
    }
  }

  @media (max-width: 760px) {
    #soroban-app { padding: 10px; }

    .intro {
      flex-direction: column;
      align-items: flex-start;
    }

    .mode-grid { grid-template-columns: 1fr; }
    .mode-card { min-height: 0; }
    .statusbar { grid-template-columns: 1fr auto; }
    .challenge-quick-nav { grid-template-columns: 1fr 1fr; }
    .review { grid-template-columns: 1fr; }

    .question-card {
      min-height: 540px;
      padding: 18px;
    }

    .equation { margin-top: 32px; }
  }
</style>

<div id="versionLabel" class="version"></div>

<div id="homeView" class="app-home">
  <div class="home-title">そろばん</div>
  <div class="home-sub">
    計算に挑戦するか、珠の動かし方を順番に学ぶかを選んでください。
  </div>

  <div class="home-rank-card">
    <div id="homeRankMedal" class="rank-medal">
      <span class="rank-medal-text">－</span>
    </div>
    <div class="home-rank-copy">
      <div class="home-rank-label">あなたの階級</div>
      <div id="homeRankName" class="home-rank-name">見習い</div>
      <div id="homeRankNext" class="app-rank-next">チャレンジでメダルを獲得しよう</div>
    </div>
  </div>

  <div class="home-mode-grid">
    <button type="button" id="homeChallengeBtn" data-app-nav="challenge" class="home-mode-btn primary-home">
      <span class="home-mode-kicker">CHALLENGE</span>
      <span class="home-mode-name">チャレンジモード</span>
      <span class="home-mode-desc">
        12問の計算トレーニング。時間制限のON/OFFや音声回答も使えます。
      </span>
    </button>

    <button type="button" id="homeLearningBtn" data-app-nav="learning" class="home-mode-btn">
      <span class="home-mode-kicker">LEARNING</span>
      <span class="home-mode-name">学習モード</span>
      <span class="home-mode-desc">
        足し算・引き算・掛け算・割り算の珠の動きを、ゆっくり学びます。
      </span>
    </button>

    <button type="button" id="homeAnzanBtn" data-app-nav="anzan" class="home-mode-btn anzan-home">
      <span class="home-mode-kicker">MENTAL</span>
      <span class="home-mode-name">暗算モード</span>
      <span class="home-mode-desc">
        数字を順番に見て頭の中で計算。スタート後は答えることに集中できます。
      </span>
    </button>

    <button type="button" id="homeSettingsBtn" data-app-nav="settings" class="home-mode-btn settings-home">
      <span class="home-mode-kicker">SETTINGS</span>
      <span class="home-mode-name">設定</span>
      <span class="home-mode-desc">
        BGM・音声・時間制限・暗算速度をまとめて設定します。
      </span>
    </button>
  </div>
</div>


<div id="anzanView" class="anzan-view">
  <div class="subview-topbar">
    <button type="button" id="anzanTopBtn" data-app-nav="home" onclick="window.__sorobanHardNav && window.__sorobanHardNav('home'); return false;" class="top-back-btn">← TOPへ</button>
    <div class="subview-topbar-title">暗算モード</div>
  </div>

  <div id="anzanMenuView">
    <div class="anzan-hero">
      <h2>見て、頭の中で計算</h2>
      <p>
        「はじめる」を押した後は数字が自動で進みます。
        そろばんを頭に思い浮かべて、最後の答えだけ入力します。
      </p>
    </div>

    <div class="anzan-level-grid">
      <article class="anzan-level-card">
        <div class="anzan-level-no">LEVEL 1</div>
        <div class="anzan-level-name">1桁・3口</div>
        <div class="anzan-level-desc">
          1〜9の足し算を3回。暗算を始める最初のステップです。
        </div>
        <button type="button" class="primary start-anzan" data-anzan-level="1">はじめる</button>
      </article>

      <article class="anzan-level-card">
        <div class="anzan-level-no">LEVEL 2</div>
        <div class="anzan-level-name">1桁・5口</div>
        <div class="anzan-level-desc">
          1桁の足し算・引き算を5回。途中の答えは0未満になりません。
        </div>
        <button type="button" class="primary start-anzan" data-anzan-level="2">はじめる</button>
      </article>

      <article class="anzan-level-card">
        <div class="anzan-level-no">LEVEL 3</div>
        <div class="anzan-level-name">2桁・5口</div>
        <div class="anzan-level-desc">
          10〜49を使う足し算・引き算。位を頭の中で保つ練習です。
        </div>
        <button type="button" class="primary start-anzan" data-anzan-level="3">はじめる</button>
      </article>
    </div>
  </div>

  <div id="anzanGameView" class="hidden">
    <div class="anzan-game-card">
      <div class="anzan-status">
        <span id="anzanQCount">1 / 10</span>
        <span id="anzanCorrectCount">正解 0</span>
        <span id="anzanSpeedLabel">ゆっくり</span>
      </div>

      <div id="anzanFlash" class="anzan-flash ready">よーい</div>
      <div id="anzanHint" class="anzan-hint">数字を順番に見てね</div>

      <div id="anzanAnswerArea" class="hidden">
        <div id="anzanAnswerDisplay" class="anzan-answer-display">&nbsp;</div>
        <div id="anzanVoiceStatus" class="anzan-hint"></div>

        <div id="anzanKeypad" class="anzan-keypad">
          <button type="button" data-anzan-key="7">7</button>
          <button type="button" data-anzan-key="8">8</button>
          <button type="button" data-anzan-key="9">9</button>
          <button type="button" data-anzan-key="4">4</button>
          <button type="button" data-anzan-key="5">5</button>
          <button type="button" data-anzan-key="6">6</button>
          <button type="button" data-anzan-key="1">1</button>
          <button type="button" data-anzan-key="2">2</button>
          <button type="button" data-anzan-key="3">3</button>
          <button type="button" data-anzan-action="clear">クリア</button>
          <button type="button" data-anzan-key="0">0</button>
          <button type="button" data-anzan-action="backspace">← 1つ消す</button>
        </div>

        <button type="button" id="anzanSubmitBtn" class="primary">答える</button>
      </div>

      <div id="anzanFeedback" class="anzan-feedback"></div>
    </div>
  </div>

  <div id="anzanResultView" class="hidden">
    <div class="anzan-result">
      <div>暗算10問終了</div>
      <div id="anzanResultScore" class="anzan-result-score">0 / 10</div>
      <div id="anzanResultText" class="anzan-hint"></div>
      <div class="anzan-actions">
        <button type="button" id="anzanRetryBtn" class="primary">同じレベルをもう10問</button>
        <button type="button" id="anzanMenuBtn">レベル選択へ</button>
      </div>
    </div>
  </div>
</div>

<div id="settingsView" class="settings-view">
  <div class="subview-topbar">
    <button type="button" id="settingsTopBtn" data-app-nav="home" onclick="window.__sorobanHardNav && window.__sorobanHardNav('home'); return false;" class="top-back-btn">← TOPへ</button>
    <div class="subview-topbar-title">設定</div>
  </div>

  <div class="settings-hero">
    <h2>設定</h2>
    <p>
      子どもが毎回設定を触らなくてよいよう、ここでまとめて保存します。
    </p>
  </div>

  <div class="settings-grid">
    <article class="setting-card">
      <div class="setting-title">チャレンジの時間制限</div>
      <div class="setting-desc">5分で挑戦するか、時間制限なしにします。</div>
      <div id="settingTimeValue" class="setting-value">5分</div>
      <button type="button" id="settingTimeBtn">切り替える</button>
    </article>

    <article class="setting-card">
      <div class="setting-title">チャレンジの音声回答</div>
      <div class="setting-desc">チャレンジ開始時から音声回答を使う設定です。</div>
      <div id="settingVoiceValue" class="setting-value">OFF</div>
      <button type="button" id="settingVoiceBtn">切り替える</button>
    </article>

    <article class="setting-card">
      <div class="setting-title">BGM</div>
      <div class="setting-desc">チャレンジ中のBGMを最初からON/OFFにします。マイクON中も音楽は流れ、答えを話した瞬間だけ自動で下がります。</div>
      <div id="settingBgmValue" class="setting-value">ON</div>
      <button type="button" id="settingBgmBtn">切り替える</button>
    </article>

    <article class="setting-card">
      <div class="setting-title">学習モードの音声解説</div>
      <div class="setting-desc">学習STEPを開いたときの音声解説の初期状態です。</div>
      <div id="settingLearningVoiceValue" class="setting-value">OFF</div>
      <button type="button" id="settingLearningVoiceBtn">切り替える</button>
    </article>

    <article class="setting-card">
      <div class="setting-title">暗算の数字表示速度</div>
      <div class="setting-desc">数字1つを画面に表示する時間です。</div>
      <div id="settingAnzanSpeedValue" class="setting-value">ゆっくり</div>
      <button type="button" id="settingAnzanSpeedBtn">速度を変える</button>
    </article>
  </div>

  <div class="app-rank-card">
    <div class="app-rank-head">
      <div>
        <div class="app-rank-title">現在のアプリ内階級</div>
        <div id="appRankSub" class="app-rank-next">クリアした難易度と正答数から自動認定します。</div>
      </div>
      <div id="appRankBadge" class="app-rank-badge">見習い</div>
    </div>
    <div class="app-rank-meter">
      <div id="appRankMeterFill" class="app-rank-meter-fill"></div>
    </div>
    <div id="appRankNext" class="app-rank-next"></div>
    <div class="app-rank-note">
      簡単な問題を繰り返すだけでは昇格しません。対応する難易度のチャレンジを認定水準でクリアした場合だけ級・段が上がります。公式の珠算検定とは別のアプリ内称号です。
    </div>
  </div>

  <div class="medal-collection">
    <div class="medal-collection-title">獲得メダル</div>
    <div class="medal-collection-note">
      認定済みの階級メダルを保存します。上位階級を獲得すると、それまでの階級メダルもコレクションへ追加されます。
    </div>
    <div id="medalCollectionGrid" class="medal-grid"></div>
  </div>

  <div class="settings-save-note">
    設定はこの端末のブラウザに自動保存されます。暗算モードは、音声回答をONにしておくと答えるときの画面操作も減らせます。
  </div>
</div>

<div id="rankCelebration" class="rank-celebration" aria-hidden="true">
  <div class="rank-burst"></div>
  <div id="rankConfettiLayer"></div>
  <div class="rank-celebration-card">
    <div class="rank-celebration-kicker">NEW MEDAL</div>
    <div id="rankCelebrationMedal" class="rank-medal rank-celebration-medal">
      <span id="rankCelebrationMedalText" class="rank-medal-text">10級</span>
    </div>
    <div id="rankCelebrationTitle" class="rank-celebration-title">10級 認定！</div>
    <div id="rankCelebrationSub" class="rank-celebration-sub"></div>
    <button type="button" id="rankCelebrationClose" class="primary">メダルを受け取る</button>
  </div>
</div>

<div id="menuView" class="hidden">
  <div class="subview-topbar">
    <button type="button" id="challengeTopBtn" data-app-nav="home" onclick="window.__sorobanHardNav && window.__sorobanHardNav('home'); return false;" class="top-back-btn">← TOPへ</button>
    <div class="subview-topbar-title">チャレンジモード</div>
  </div>
  <div class="intro">
    <div class="lead">
      各モード12問です。
      割り算以外は、計算式を確定する直前に左右の数へ必ず+1します。
      そのため表示される数に0は出ません。
    </div>
    <div id="modeTimeBadge" class="badge">12問 / 5分</div>
  </div>

  <div class="time-limit-control">
    <span class="time-limit-label">時間制限</span>
    <button type="button" id="timeLimitBtn" class="time-limit-toggle">
      5分
    </button>
  </div>

  <div class="analysis-control">
    <div>
      <div class="analysis-control-title">🎥 動画で振り返り</div>
      <div class="analysis-control-note">
        インカメラで手元とそろばんを撮影し、問題ごとの時刻と自動で同期します。
        録画は映像のみで、音声認識用マイクとは分けます。
      </div>
    </div>
    <button type="button" id="analysisVideoToggle" class="time-limit-toggle">
      OFF
    </button>
  </div>

  <div class="level-title">初級モード｜足し算のみ</div>
  <div class="mode-grid">
    <article class="mode-card">
      <div class="mode-no">BEGINNER ADD 1</div>
      <div class="mode-name">1桁・繰り上がりなし</div>
      <div class="mode-desc">1〜9同士の足し算。一の位から十の位への繰り上がりがない12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="ba1">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER ADD 2</div>
      <div class="mode-name">1桁・繰り上がりあり</div>
      <div class="mode-desc">1〜9同士の足し算。一の位から十の位へ必ず繰り上がる12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="ba2">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER ADD 3</div>
      <div class="mode-name">2桁・繰り上がりなし</div>
      <div class="mode-desc">10〜99同士。一の位から十の位への繰り上がりがない2桁レベルです。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="ba3">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER ADD 4</div>
      <div class="mode-name">2桁・繰り上がりあり</div>
      <div class="mode-desc">10〜99同士。一の位から十の位へ必ず繰り上がる2桁レベルです。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="ba4">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER ADD 5</div>
      <div class="mode-name">3桁の足し算</div>
      <div class="mode-desc">100〜999同士の足し算。従来の3桁レベルです。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="ba5">はじめる</button>
    </article>
  </div>

  <div class="level-title">初級モード｜引き算のみ</div>
  <div class="mode-grid">
    <article class="mode-card">
      <div class="mode-no">BEGINNER SUB 1</div>
      <div class="mode-name">1桁・繰り下がりなし</div>
      <div class="mode-desc">1〜9同士。一の位だけで引ける、繰り下がりなしの12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="bs1">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER SUB 2</div>
      <div class="mode-name">1桁を引く・繰り下がりあり</div>
      <div class="mode-desc">10〜18から1〜9を引き、一の位の不足で十の位から必ず繰り下がる12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="bs2">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER SUB 3</div>
      <div class="mode-name">2桁・繰り下がりなし</div>
      <div class="mode-desc">10〜99同士。一の位から十の位への繰り下がりがない2桁レベルです。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="bs3">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER SUB 4</div>
      <div class="mode-name">2桁・繰り下がりあり</div>
      <div class="mode-desc">10〜99同士。一の位の不足で十の位から必ず繰り下がる2桁レベルです。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="bs4">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">BEGINNER SUB 5</div>
      <div class="mode-name">3桁の引き算</div>
      <div class="mode-desc">100〜999同士の引き算。従来の3桁レベルです。答えは0以上です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="bs5">はじめる</button>
    </article>
  </div>

  <div class="level-title">中級モード｜掛け算のみ</div>
  <div class="mode-grid">
    <article class="mode-card">
      <div class="mode-no">INTERMEDIATE MUL 1</div>
      <div class="mode-name">1桁×1桁</div>
      <div class="mode-desc">1〜9同士の掛け算のみ12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="mm1">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">INTERMEDIATE MUL 2</div>
      <div class="mode-name">2桁×1桁</div>
      <div class="mode-desc">2桁×1桁の掛け算のみ12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="mm2">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">INTERMEDIATE MUL 3</div>
      <div class="mode-name">2桁×2桁</div>
      <div class="mode-desc">2桁同士の掛け算のみ12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="mm3">はじめる</button>
    </article>
  </div>

  <div class="level-title">中級モード｜割り算のみ</div>
  <div class="mode-grid">
    <article class="mode-card">
      <div class="mode-no">INTERMEDIATE DIV 1</div>
      <div class="mode-name">1〜81 ÷ 1桁</div>
      <div class="mode-desc">1〜81を1桁で割り、答えが整数になる割り算のみ12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="md1">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">INTERMEDIATE DIV 2</div>
      <div class="mode-name">3桁÷1桁</div>
      <div class="mode-desc">100〜999を1桁で割り、答えが整数になる割り算のみ12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="md2">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">INTERMEDIATE DIV 3</div>
      <div class="mode-name">3桁÷2桁</div>
      <div class="mode-desc">100〜999を2桁で割り、答えが整数になる割り算のみ12問です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="md3">はじめる</button>
    </article>
  </div>

  <div class="level-title">上級モード</div>
  <div class="mode-grid">
    <article class="mode-card">
      <div class="mode-no">ADVANCED 1</div>
      <div class="mode-name">4桁の足し算・引き算</div>
      <div class="mode-desc">最終表示は1000〜9999。引き算の答えは0以上です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="a1">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">ADVANCED 2</div>
      <div class="mode-name">3桁×2桁 / 4桁÷2桁</div>
      <div class="mode-desc">3桁×2桁の掛け算と、4桁÷2桁の整数解です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="a2">はじめる</button>
    </article>

    <article class="mode-card">
      <div class="mode-no">ADVANCED 3</div>
      <div class="mode-name">3桁×3桁 / 5桁÷3桁</div>
      <div class="mode-desc">3桁同士の掛け算と、5桁÷3桁の整数解です。</div>
      <div class="chips"><span class="chip">12問</span><span class="chip time-chip">5分</span></div>
      <button type="button" class="primary start-mode" data-mode="a3">はじめる</button>
    </article>
  </div>

  <div id="rankingModal" class="ranking-modal" aria-hidden="true">
    <div class="ranking-dialog">
      <div class="ranking-dialog-head">
        <div id="rankingModalTitle" class="ranking-dialog-title">
          過去の得点ランキング
        </div>
        <button type="button" id="rankingModalClose" class="ranking-dialog-close">
          閉じる
        </button>
      </div>
      <div class="ranking-note">
        このブラウザに保存された記録です。親御さんの記録などは「削除」で1件ずつ消せます。
      </div>
      <div id="rankingModalList" class="ranking-list"></div>
    </div>
  </div>
</div>


<div id="learningView" class="learning-view">
  <div class="subview-topbar">
    <button type="button" id="learningTopBtn" data-app-nav="home" onclick="window.__sorobanHardNav && window.__sorobanHardNav('home'); return false;" class="top-back-btn">← TOPへ</button>
    <div class="subview-topbar-title">学習モード</div>
  </div>

  <div id="learningCategoryView">
    <div class="learning-hero">
      <h2>珠の動かし方を順番に学ぶ</h2>
      <p>
        まず動画のような珠の動きを見て、ポイントを確認し、最後に理解チェックをします。
        各計算はSTEP 1から少しずつ難しくなります。
      </p>
      <div id="learningOverallText" class="learning-progress-summary"></div>
      <div class="learning-progress-track">
        <div id="learningOverallBar" class="learning-progress-bar"></div>
      </div>
    </div>

    <div id="learningCategoryGrid" class="learning-category-grid"></div>
  </div>

  <div id="learningStageView" class="hidden">
    <div class="learning-lesson-head">
      <div>
        <div id="learningStageKicker" class="learning-lesson-kicker"></div>
        <div id="learningStageHeading" class="learning-lesson-title"></div>
      </div>
      <button type="button" id="learningStageBackBtn">← 計算の種類へ</button>
    </div>

    <div id="learningStageGrid" class="learning-stage-grid"></div>
  </div>

  <div id="learningLessonView" class="hidden">
    <div class="learning-lesson-head">
      <div>
        <div id="learningLessonKicker" class="learning-lesson-kicker"></div>
        <div id="learningLessonTitle" class="learning-lesson-title"></div>
      </div>
      <button type="button" id="learningLessonBackBtn">← STEP一覧へ</button>
    </div>

    <div class="learning-example-card">
      <div class="learning-example-label">この問題の珠の動きを見ます</div>
      <div id="learningExampleProblem" class="learning-example-problem"></div>
      <div class="learning-example-help">
        まず問題全体を見てから、下の珠がどう動くかをゆっくり追ってください。
      </div>
    </div>

    <div id="learningVideoCard" class="learning-video-card">
      <div class="learning-video-title">
        <span>珠の動き動画</span>
        <span class="learning-video-badge">自動アニメーション</span>
      </div>

      <div id="learningStepText" class="learning-step-text"></div>

      <div class="learning-soroban-wrap">
        <div id="learningSorobanBoard" class="learning-soroban-board"></div>
      </div>

      <div id="learningFrameCounter" class="learning-frame-counter"></div>

      <div class="learning-video-controls">
        <button type="button" id="learningReplayBtn">↻ もう一度</button>
        <button type="button" id="learningPauseBtn">Ⅱ 一時停止</button>
        <button type="button" id="learningVoiceBtn" class="learning-voice-btn">
          🔊 音声解説 OFF
        </button>
      </div>
      <div id="learningVoiceNote" class="learning-voice-note">
        音声解説をONにすると、珠の動きをゆっくり読み上げます。
      </div>
    </div>

    <div class="learning-explain-card">
      <div class="learning-point-grid">
        <div class="learning-point">
          <div class="learning-point-label">今回の基本</div>
          <div id="learningBasicText" class="learning-point-text"></div>
        </div>
        <div class="learning-point trouble">
          <div class="learning-point-label">つまずきやすいところ</div>
          <div id="learningTroubleText" class="learning-point-text"></div>
        </div>
      </div>

      <div id="learningRuleBox" class="learning-rule-box"></div>
    </div>

    <div class="learning-quiz-card">
      <div class="learning-video-title">
        <span>理解チェック</span>
        <span class="learning-video-badge">クリアで ★</span>
      </div>
      <div id="learningQuizQuestion" class="learning-quiz-question"></div>
      <div id="learningQuizOptions" class="learning-quiz-options"></div>
      <div id="learningQuizFeedback" class="learning-quiz-feedback"></div>

      <div id="learningClearActions" class="learning-clear-actions">
        <button type="button" id="learningAgainBtn">動画をもう一度</button>
        <button type="button" id="learningNextBtn" class="primary">次のSTEPへ</button>
      </div>
    </div>
  </div>
</div>

<div id="cameraSetupModal" class="camera-modal">
  <div class="camera-dialog">
    <h3>撮影準備｜画角を確認</h3>
    <p>
      <strong>両手とそろばん全体を下の白い枠へ。</strong>
      顔は上の小さい枠に一部入れば十分です。
    </p>

    <div class="camera-preview-wrap">
      <video
        id="analysisCameraPreview"
        class="camera-preview"
        autoplay
        muted
        playsinline
      ></video>
      <div class="camera-face-guide">
        <div class="camera-face-label">顔はこの辺り</div>
      </div>
      <div class="camera-guide">
        <div class="camera-guide-label">両手＋そろばん全体をここへ</div>
      </div>
    </div>

    <div id="cameraSetupStatus" class="camera-status">
      カメラを準備しています…
    </div>

    <div class="camera-actions">
      <button type="button" id="cameraCancelBtn">キャンセル</button>
      <button type="button" id="cameraNoVideoBtn">撮影なしで開始</button>
      <button type="button" id="cameraBeginBtn" class="primary" disabled>
        この画角で開始
      </button>
    </div>
  </div>
</div>

<section id="workspace" class="workspace">
  <div class="statusbar">
    <div>
      <div id="statusTitle" class="status-title"></div>
      <div id="statusSub" class="status-sub">12問・制限時間5分</div>
    </div>
    <div id="timer" class="timer">5:00</div>
    <div id="streak" class="streak">連続正解 0</div>
    <button type="button" id="voiceBtn" class="voice-btn">🎤 音声回答 OFF</button>
    <button type="button" id="bgmBtn">♪ BGM ON</button>
    <div id="recordingBadge" class="recording-badge">● REC</div>
    <div class="challenge-quick-nav">
      <button type="button" id="challengePlayTopBtn" data-app-nav="home" onclick="window.__sorobanHardNav && window.__sorobanHardNav('home'); return false;">← TOPへ</button>
      <button type="button" id="quitBtn" data-app-nav="challenge-menu" onclick="window.__sorobanHardNav && window.__sorobanHardNav('challenge-menu'); return false;">モード選択へ</button>
    </div>
    <div id="voiceStatus" class="voice-status">音声回答：OFF</div>
    <div id="bgmName" class="bgm-name">BGM：準備中</div>
  </div>

  <div id="questionCard" class="question-card">
    <div class="qtop">
      <div>
        <span id="qCount" class="qcount">1 / 12</span>
        <span id="weakBadge" class="weak-badge">にがて克服</span>
      </div>
      <div id="score" class="score">正解 0</div>
    </div>

    <div id="equation" class="equation"></div>

    <div class="hint-launch">
      <button type="button" id="hintBtn" class="hint-btn">
        珠ヒントを見る（手動＋音声）
      </button>
    </div>

    <div id="abacusHint" class="abacus-hint">
      <div class="hint-head">
        <div class="hint-title">そろばんの珠の動かし方｜1つずつ進む</div>
        <div class="hint-actions">
          <button type="button" id="hintCloseBtn">閉じる</button>
        </div>
      </div>

      <div id="hintStep" class="hint-step"></div>

      <div class="hint-manual-nav">
        <button type="button" id="hintPrevBtn">← 戻る</button>
        <div id="hintStepCounter" class="hint-manual-counter">STEP 1 / 1</div>
        <button type="button" id="hintNextBtn" class="primary">進む →</button>
      </div>

      <div class="soroban-scroll">
        <div id="sorobanBoard" class="soroban-board"></div>
      </div>

      <div class="hint-legend">
        「進む」を押すと次の珠の状態へ進みます。黄色い位が、いま考えているところです。
      </div>
    </div>

    <form id="answerForm" class="answer-form">
      <input
        id="answerInput"
        class="answer-input"
        type="text"
        inputmode="none"
        autocomplete="off"
        aria-label="答え"
        placeholder="答え"
        readonly
      >
      <button type="submit" class="primary">答える</button>
    </form>

    <div class="keypad-wrap">
      <div class="keypad-label">数字をタップして入力</div>
      <div id="keypad" class="keypad">
        <button type="button" data-key="7">7</button>
        <button type="button" data-key="8">8</button>
        <button type="button" data-key="9">9</button>
        <button type="button" data-key="4">4</button>
        <button type="button" data-key="5">5</button>
        <button type="button" data-key="6">6</button>
        <button type="button" data-key="1">1</button>
        <button type="button" data-key="2">2</button>
        <button type="button" data-key="3">3</button>
        <button type="button" class="key-action" data-action="clear">クリア</button>
        <button type="button" data-key="0">0</button>
        <button type="button" class="key-action" data-action="backspace">← 1つ消す</button>
      </div>
    </div>

    <div id="feedback" class="feedback"></div>

    <div class="progress">
      <div id="bar" class="bar"></div>
    </div>
  </div>

  <div id="results" class="results">
    <div class="result-note">結果</div>

    <div id="scoreStage" class="score-stage">
      <div class="score-caption">チャレンジスコア</div>
      <div id="resultScore" class="result-score">---</div>
      <div id="scoreFormula" class="score-formula"></div>
    </div>

    <div id="resultNote" class="result-note"></div>

    <div id="resultRankingPanel" class="ranking-panel">
      <div class="ranking-panel-title">このモードの過去の得点ランキング</div>
      <div class="ranking-note">
        点数は小さめに表示しています。不要な記録は1件ずつ削除できます。
      </div>
      <div id="resultRankingList" class="ranking-list"></div>
    </div>

    <div id="videoReviewPanel" class="video-review-panel hidden">
      <div class="video-review-title">今回の課題と次の練習</div>
      <div class="video-review-note">
        正誤・回答時間・珠ヒント使用などのログから、復習する内容を短く整理します。
        動画の問題別再生は表示せず、解説と補習を優先します。
      </div>

      <div class="coaching-rank-strip">
        <div>
          <div class="coaching-rank-label">現在のアプリ内階級</div>
          <div id="resultRankTitle" class="coaching-rank-title">見習い</div>
        </div>
        <div id="resultRankProgress" class="video-question-meta"></div>
      </div>

      <div id="coachingIssueList" class="coaching-issues"></div>

      <div id="videoReviewStatus" class="camera-status">
        学習ログはこの端末内に自動保存されます。
      </div>

      <div class="video-review-actions">
        <button type="button" id="downloadSessionVideoBtn">録画動画を保存（任意）</button>
        <button type="button" id="downloadSessionLogBtn">今回のログを保存</button>
      </div>
    </div>

    <div id="review" class="review"></div>
    <div class="actions">
      <button type="button" id="retryBtn" class="primary">同じモードをもう一度</button>
      <button type="button" id="menuBtn" data-app-nav="challenge-menu" onclick="window.__sorobanHardNav && window.__sorobanHardNav('challenge-menu'); return false;">モード選択へ</button>
      <button type="button" id="resultTopBtn" data-app-nav="home" onclick="window.__sorobanHardNav && window.__sorobanHardNav('home'); return false;">← TOPへ</button>
    </div>
  </div>
</section>

<script>
(() => {
  const APP_VERSION = __APP_VERSION__;
  const BGM_URLS = __BGM_URLS__;

  const root = document.getElementById("soroban-app");
  const $ = (selector) => root.querySelector(selector);

  window.addEventListener(
    "error",
    (event) => {
      try {
        root.dataset.runtimeError =
          String(event.message || "runtime error");
      } catch (error) {}
    }
  );

  // Navigation is deliberately dependency-free and installed before all other app logic.
  // It changes only view classes using raw DOM queries, so even a later JS error cannot trap the user.
  function hardSetView(target) {
    const byId = (id) => root.querySelector(`#${id}`);
    const home = byId("homeView");
    const menu = byId("menuView");
    const learning = byId("learningView");
    const anzan = byId("anzanView");
    const settings = byId("settingsView");
    const workspaceEl = byId("workspace");
    const resultsEl = byId("results");

    if (!home || !menu || !learning || !anzan || !settings || !workspaceEl || !resultsEl) {
      return false;
    }

    const hideEverything = () => {
      home.classList.add("hidden");
      menu.classList.add("hidden");
      learning.classList.remove("show");
      anzan.classList.remove("show");
      settings.classList.remove("show");
      workspaceEl.classList.remove("show");
      resultsEl.classList.remove("show");
    };

    hideEverything();

    if (target === "home") {
      home.classList.remove("hidden");
    } else if (target === "challenge" || target === "challenge-menu") {
      menu.classList.remove("hidden");
    } else if (target === "learning") {
      learning.classList.add("show");
      const category = byId("learningCategoryView");
      const stage = byId("learningStageView");
      const lesson = byId("learningLessonView");
      if (category) category.classList.remove("hidden");
      if (stage) stage.classList.add("hidden");
      if (lesson) lesson.classList.add("hidden");
    } else if (target === "anzan") {
      anzan.classList.add("show");
      const menuViewEl = byId("anzanMenuView");
      const gameViewEl = byId("anzanGameView");
      const resultViewEl = byId("anzanResultView");
      if (menuViewEl) menuViewEl.classList.remove("hidden");
      if (gameViewEl) gameViewEl.classList.add("hidden");
      if (resultViewEl) resultViewEl.classList.add("hidden");
    } else if (target === "settings") {
      settings.classList.add("show");
    } else {
      return false;
    }

    return true;
  }

  let lastHardNavAt = 0;
  let lastHardNavTarget = "";

  function hardNavigate(target) {
    const now = Date.now();
    if (target === lastHardNavTarget && now - lastHardNavAt < 450) {
      return;
    }

    lastHardNavAt = now;
    lastHardNavTarget = target;

    hardSetView(target);

    // Cleanup is secondary. The view has already changed even if cleanup fails.
    setTimeout(() => {
      try {
        if (target === "home") showHome();
        else if (target === "challenge-menu") goMenu();
        else if (target === "challenge") showChallengeMenu();
        else if (target === "learning") showLearningHome();
        else if (target === "anzan") showAnzanHome();
        else if (target === "settings") showSettings();
      } catch (error) {}
    }, 0);
  }

  window.__sorobanHardNav = hardNavigate;

  function handleHardNavEvent(event) {
    const button = event.target.closest("[data-app-nav]");
    if (!button || !root.contains(button)) return;

    event.preventDefault();
    event.stopPropagation();

    hardNavigate(button.dataset.appNav);
  }

  // pointerup is primary on phones; click is a fallback for keyboard/mouse/browser quirks.
  root.addEventListener("pointerup", handleHardNavEvent, true);
  root.addEventListener("click", handleHardNavEvent, true);

  $("#versionLabel").textContent = `APP VERSION: ${APP_VERSION}`;

  const homeView = $("#homeView");
  const anzanView = $("#anzanView");
  const anzanMenuView = $("#anzanMenuView");
  const anzanGameView = $("#anzanGameView");
  const anzanResultView = $("#anzanResultView");
  const settingsView = $("#settingsView");
  const menuView = $("#menuView");
  const learningView = $("#learningView");
  const learningCategoryView = $("#learningCategoryView");
  const learningStageView = $("#learningStageView");
  const learningLessonView = $("#learningLessonView");
  const learningCategoryGrid = $("#learningCategoryGrid");
  const learningStageGrid = $("#learningStageGrid");
  const learningOverallText = $("#learningOverallText");
  const learningOverallBar = $("#learningOverallBar");
  const learningStageKicker = $("#learningStageKicker");
  const learningStageHeading = $("#learningStageHeading");
  const learningLessonKicker = $("#learningLessonKicker");
  const learningLessonTitle = $("#learningLessonTitle");
  const learningExampleProblem = $("#learningExampleProblem");
  const learningVideoCard = $("#learningVideoCard");
  const learningStepText = $("#learningStepText");
  const learningSorobanBoard = $("#learningSorobanBoard");
  const learningFrameCounter = $("#learningFrameCounter");
  const learningBasicText = $("#learningBasicText");
  const learningTroubleText = $("#learningTroubleText");
  const learningRuleBox = $("#learningRuleBox");
  const learningQuizQuestion = $("#learningQuizQuestion");
  const learningQuizOptions = $("#learningQuizOptions");
  const learningQuizFeedback = $("#learningQuizFeedback");
  const learningClearActions = $("#learningClearActions");
  const learningPauseBtn = $("#learningPauseBtn");
  const learningVoiceBtn = $("#learningVoiceBtn");
  const learningVoiceNote = $("#learningVoiceNote");
  const learningNextBtn = $("#learningNextBtn");

  const anzanFlash = $("#anzanFlash");
  const anzanHint = $("#anzanHint");
  const anzanAnswerArea = $("#anzanAnswerArea");
  const anzanAnswerDisplay = $("#anzanAnswerDisplay");
  const anzanFeedback = $("#anzanFeedback");
  const anzanVoiceStatus = $("#anzanVoiceStatus");
  const anzanQCount = $("#anzanQCount");
  const anzanCorrectCount = $("#anzanCorrectCount");
  const anzanSpeedLabel = $("#anzanSpeedLabel");
  const anzanResultScore = $("#anzanResultScore");
  const anzanResultText = $("#anzanResultText");

  const settingTimeValue = $("#settingTimeValue");
  const settingVoiceValue = $("#settingVoiceValue");
  const settingBgmValue = $("#settingBgmValue");
  const settingLearningVoiceValue = $("#settingLearningVoiceValue");
  const settingAnzanSpeedValue = $("#settingAnzanSpeedValue");
  const appRankBadge = $("#appRankBadge");
  const appRankMeterFill = $("#appRankMeterFill");
  const appRankSub = $("#appRankSub");
  const appRankNext = $("#appRankNext");
  const homeRankMedal = $("#homeRankMedal");
  const homeRankName = $("#homeRankName");
  const homeRankNext = $("#homeRankNext");
  const medalCollectionGrid = $("#medalCollectionGrid");
  const rankCelebration = $("#rankCelebration");
  const rankConfettiLayer = $("#rankConfettiLayer");
  const rankCelebrationMedal = $("#rankCelebrationMedal");
  const rankCelebrationMedalText = $("#rankCelebrationMedalText");
  const rankCelebrationTitle = $("#rankCelebrationTitle");
  const rankCelebrationSub = $("#rankCelebrationSub");

  const workspace = $("#workspace");
  const questionCard = $("#questionCard");
  const results = $("#results");
  const equation = $("#equation");
  const answerInput = $("#answerInput");
  const feedback = $("#feedback");
  const timerEl = $("#timer");
  const statusSub = $("#statusSub");
  const timeLimitBtn = $("#timeLimitBtn");
  const modeTimeBadge = $("#modeTimeBadge");
  const analysisVideoToggle = $("#analysisVideoToggle");
  const cameraSetupModal = $("#cameraSetupModal");
  const analysisCameraPreview = $("#analysisCameraPreview");
  const cameraSetupStatus = $("#cameraSetupStatus");
  const cameraBeginBtn = $("#cameraBeginBtn");
  const recordingBadge = $("#recordingBadge");
  const bar = $("#bar");
  const scoreStage = $("#scoreStage");
  const resultScore = $("#resultScore");
  const scoreFormula = $("#scoreFormula");
  const resultRankingList = $("#resultRankingList");
  const resultRankingPanel = $("#resultRankingPanel");
  const videoReviewPanel = $("#videoReviewPanel");
  const videoReviewStatus = $("#videoReviewStatus");
  const coachingIssueList = $("#coachingIssueList");
  const resultRankTitle = $("#resultRankTitle");
  const resultRankProgress = $("#resultRankProgress");
  const rankingModal = $("#rankingModal");
  const rankingModalTitle = $("#rankingModalTitle");
  const rankingModalList = $("#rankingModalList");

  const voiceBtn = $("#voiceBtn");
  const voiceStatus = $("#voiceStatus");
  const bgmBtn = $("#bgmBtn");
  const bgmName = $("#bgmName");
  const hintBtn = $("#hintBtn");
  const hintPrevBtn = $("#hintPrevBtn");
  const hintNextBtn = $("#hintNextBtn");
  const hintStepCounter = $("#hintStepCounter");
  const abacusHint = $("#abacusHint");
  const hintStep = $("#hintStep");
  const sorobanBoard = $("#sorobanBoard");

  // TOPの4ボタンは、メダル・ログ・カメラなどの初期化より先に独立して登録する。
  // 追加機能側で例外が起きても、TOPから各モードへ移動できるようにする。
  // Primary navigation is handled by the delegated data-app-nav listener.

  const modeInfo = {
    ba1: { title: "初級・足し算1｜1桁・繰り上がりなし" },
    ba2: { title: "初級・足し算2｜1桁・繰り上がりあり" },
    ba3: { title: "初級・足し算3｜2桁・繰り上がりなし" },
    ba4: { title: "初級・足し算4｜2桁・繰り上がりあり" },
    ba5: { title: "初級・足し算5｜3桁の足し算" },
    bs1: { title: "初級・引き算1｜1桁・繰り下がりなし" },
    bs2: { title: "初級・引き算2｜1桁を引く・繰り下がりあり" },
    bs3: { title: "初級・引き算3｜2桁・繰り下がりなし" },
    bs4: { title: "初級・引き算4｜2桁・繰り下がりあり" },
    bs5: { title: "初級・引き算5｜3桁の引き算" },
    mm1: { title: "中級・掛け算1｜1桁×1桁" },
    mm2: { title: "中級・掛け算2｜2桁×1桁" },
    mm3: { title: "中級・掛け算3｜2桁×2桁" },
    md1: { title: "中級・割り算1｜1〜81÷1桁" },
    md2: { title: "中級・割り算2｜3桁÷1桁" },
    md3: { title: "中級・割り算3｜3桁÷2桁" },
    a1: { title: "上級1｜4桁の足し算・引き算" },
    a2: { title: "上級2｜3桁×2桁 / 4桁÷2桁" },
    a3: { title: "上級3｜3桁×3桁 / 5桁÷3桁" }
  };

  const LEARNING_PROGRESS_STORAGE_KEY =
    "soroban_tutorial_progress_v1";

  const learningCategories = {
    add: {
      symbol: "＋",
      title: "足し算",
      desc: "一珠・五珠から、5の補数、10の補数、2桁の繰り上がりへ進みます。",
      lessons: [
        {
          id: "add1",
          example: "2 ＋ 2 ＝ ?",
          title: "1〜4をそのまま足す",
          desc: "使える一珠を、そのまま梁へ寄せる基本。",
          basic: "足したい数だけ一珠が残っているときは、同じ位の一珠を梁へ寄せます。まずは珠を数えず、形で1〜4を見分けることが大切です。",
          trouble: "指で珠を1個ずつ数え続けると速くなりません。『2の形』『3の形』を一目で見る練習をします。",
          rule: "足せる珠がある → そのまま足す。",
          frames: [
            { value: 2, place: 1, text: "まず 2 を置きます。" },
            { value: 3, place: 1, text: "＋2 の1個目。1の珠を1つ梁へ寄せます。" },
            { value: 4, place: 1, text: "＋2 の2個目。もう1つ寄せて 4 です。" }
          ],
          quiz: { q: "2 ＋ 2 の答えは？", options: ["3", "4", "5"], answer: "4" }
        },
        {
          id: "add2",
          example: "5 ＋ 3 ＝ ?",
          title: "5珠と1珠を組み合わせる",
          desc: "5〜9の形をすばやく作る。",
          basic: "5は上の五珠を梁へ寄せます。6〜9は『5＋一珠』の形です。たとえば8は、五珠1つと一珠3つです。",
          trouble: "6〜9を一珠6個のように考えないこと。『5といくつ』で見ると珠の形が安定します。",
          rule: "6〜9は『5 ＋ 1〜4』で見る。",
          frames: [
            { value: 5, place: 1, text: "まず 5。上の五珠を梁へ寄せます。" },
            { value: 6, place: 1, text: "＋3 の1個目。一珠を1つ寄せます。" },
            { value: 7, place: 1, text: "2個目を寄せます。" },
            { value: 8, place: 1, text: "3個目を寄せて 8 です。" }
          ],
          quiz: { q: "そろばんの 8 は『5 ＋ いくつ』？", options: ["2", "3", "4"], answer: "3" }
        },
        {
          id: "add3",
          example: "2 ＋ 4 ＝ ?",
          title: "5の補数で足す",
          desc: "一珠が足りないときに『＋5 − 補数』を使う。",
          basic: "同じ位で一珠が足りないときは、先に5の補数を引いてから五珠を入れます。2＋4なら、4の5に対する補数は1なので『−1 → ＋5』です。",
          trouble: "『何を引くか』を毎回数えないこと。1と4、2と3を5の組として覚えます。",
          rule: "＋4 は「−1 → ＋5」／ ＋3 は「−2 → ＋5」の順。",
          frames: [
            { value: 2, place: 1, text: "2 ＋ 4。2を置きます。" },
            { value: 1, place: 1, text: "4をそのまま足せないので、先に補数の −1。2 → 1。" },
            { value: 6, place: 1, text: "そのあと ＋5。答えは 6。" }
          ],
          quiz: { q: "2 ＋ 4 では、最初にどの珠を動かす？", options: ["−1", "＋5", "＋10"], answer: "−1" }
        },
        {
          id: "add4",
          example: "8 ＋ 7 ＝ ?",
          title: "10の補数で繰り上がる",
          desc: "9を超えるときに『＋10 − 補数』を使う。",
          basic: "一の位で9を超えるときは、先に一の位から10の補数を引き、そのあと十の位へ1を足します。8＋7なら、先に−3、そのあと＋10です。",
          trouble: "順番を逆にしないこと。『一の位から補数を引く → 十の位を1上げる』の順に固定します。",
          rule: "＋7 は「−3 → ＋10」／ ＋8 は「−2 → ＋10」／ ＋9 は「−1 → ＋10」の順。",
          frames: [
            { value: 8, place: 1, text: "8 ＋ 7。まず8を置きます。" },
            { value: 5, place: 1, text: "10をまたぐので、先に7の補数3を一の位から −3。8 → 5。" },
            { value: 15, place: 10, text: "そのあと十の位へ ＋10。答えは15。" }
          ],
          quiz: { q: "8 ＋ 7 で使う10の補数は？", options: ["2", "3", "4"], answer: "3" }
        },
        {
          id: "add5",
          example: "36 ＋ 27 ＝ ?",
          title: "2桁の繰り上がり",
          desc: "十の位と一の位を分けて処理する。",
          basic: "2桁は大きい位から足します。36＋27なら、まず＋20で56。そのあと一の位の＋7は『−3 → ＋10』の順で処理します。",
          trouble: "20と7を一度に処理しようとしないこと。『十の位 → 一の位』と分けると、どこで繰り上がったかが見えます。",
          rule: "2桁は位ごとに分解。大きい位から順番に。",
          frames: [
            { value: 36, place: null, text: "36 ＋ 27。まず36を置きます。" },
            { value: 56, place: 10, text: "27の十の位、＋20。36 → 56。" },
            { value: 53, place: 1, text: "一の位の＋7は10をまたぐので、先に補数3を −3。56 → 53。" },
            { value: 63, place: 10, text: "そのあと ＋10。答えは63。" }
          ],
          quiz: { q: "36 ＋ 27 で最初に足すのは？", options: ["7", "20", "27全部"], answer: "20" }
        }
      ]
    },
    sub: {
      symbol: "－",
      title: "引き算",
      desc: "そのまま引くところから、5の補数、10の補数、2桁の繰り下がりへ。",
      lessons: [
        {
          id: "sub1",
          example: "4 － 2 ＝ ?",
          title: "そのまま引く",
          desc: "取れる一珠があるときは直接梁から離す。",
          basic: "引きたい数だけ一珠が梁に寄っているときは、その珠を梁から離します。",
          trouble: "答えの珠を新しく作るのではなく、『今ある珠から取る』と考えます。",
          rule: "取れる珠がある → そのまま引く。",
          frames: [
            { value: 4, place: 1, text: "4 − 2。まず4を置きます。" },
            { value: 3, place: 1, text: "一珠を1つ梁から離します。" },
            { value: 2, place: 1, text: "もう1つ離して、答えは2。" }
          ],
          quiz: { q: "4 − 2 の答えは？", options: ["1", "2", "3"], answer: "2" }
        },
        {
          id: "sub2",
          example: "8 － 3 ＝ ?",
          title: "5珠から直接引く",
          desc: "8−3など、五珠を残して一珠を取る。",
          basic: "8は5＋3です。8−3なら、一珠3つをそのまま梁から離せば5が残ります。",
          trouble: "五珠まで動かしてしまわないこと。まず『取れる一珠が何個あるか』を見ます。",
          rule: "8 −3 → 一珠3つをそのまま取る。",
          frames: [
            { value: 8, place: 1, text: "8は『5＋3』の形です。" },
            { value: 7, place: 1, text: "一珠を1つ取ります。" },
            { value: 6, place: 1, text: "2つ目を取ります。" },
            { value: 5, place: 1, text: "3つ目を取って5。" }
          ],
          quiz: { q: "8 − 3 では、最初にどの珠を見る？", options: ["一珠", "十の位", "全部動かす"], answer: "一珠" }
        },
        {
          id: "sub3",
          example: "7 － 4 ＝ ?",
          title: "5の補数で引く",
          desc: "一珠だけでは引けないときに『−5＋補数』。",
          basic: "7−4では、一珠は2個しかないため4を直接取れません。そこで−5して、4の5に対する補数1を＋1します。",
          trouble: "『−5したから終わり』ではありません。必ず補数を戻します。",
          rule: "−4 ＝ −5 ＋1 ／ −3 ＝ −5 ＋2。",
          frames: [
            { value: 7, place: 1, text: "7 − 4。7を置きます。" },
            { value: 2, place: 1, text: "4を直接取れないので、まず −5。7 → 2。" },
            { value: 3, place: 1, text: "4の補数1を ＋1。答えは3。" }
          ],
          quiz: { q: "7 − 4 の珠の動きは？", options: ["−5＋1", "−5＋2", "−10＋4"], answer: "−5＋1" }
        },
        {
          id: "sub4",
          example: "12 － 7 ＝ ?",
          title: "10の補数で繰り下がる",
          desc: "一の位だけでは引けないときに『−10＋補数』。",
          basic: "12−7では一の位の2から7を引けません。十の位から1を下げて−10し、一の位へ7の10の補数3を足します。",
          trouble: "『借りた10』を頭の中だけで処理しないこと。十の位の珠を実際に1つ下げる動きを先にします。",
          rule: "−7 ＝ −10 ＋3 ／ −8 ＝ −10 ＋2 ／ −9 ＝ −10 ＋1。",
          frames: [
            { value: 12, place: 1, text: "12 − 7。まず12を置きます。" },
            { value: 2, place: 10, text: "一の位だけでは引けないので、十の位から −10。" },
            { value: 5, place: 1, text: "7の補数3を一の位へ ＋3。答えは5。" }
          ],
          quiz: { q: "12 − 7 で足し戻す数は？", options: ["2", "3", "7"], answer: "3" }
        },
        {
          id: "sub5",
          example: "43 － 28 ＝ ?",
          title: "2桁の繰り下がり",
          desc: "43−28を位ごとに処理する。",
          basic: "43−28なら、まず−20で23。そのあと−8は一の位だけでは引けないので『−10＋2』にします。",
          trouble: "28を一気に引かないこと。十の位20と一の位8を分ければ、補数を使う場所が明確になります。",
          rule: "2桁の引き算も、大きい位から順番に。",
          frames: [
            { value: 43, place: null, text: "43 − 28。まず43を置きます。" },
            { value: 23, place: 10, text: "十の位の −20。43 → 23。" },
            { value: 13, place: 10, text: "一の位の−8は直接できないので、まず −10。" },
            { value: 15, place: 1, text: "8の補数2を ＋2。答えは15。" }
          ],
          quiz: { q: "43 − 28 で最初に引くのは？", options: ["8", "20", "28全部"], answer: "20" }
        }
      ]
    },
    mul: {
      symbol: "×",
      title: "掛け算",
      desc: "九九→部分積→位取り。答えをどの位の珠へ置くかを段階的に学びます。",
      lessons: [
        {
          id: "mul1",
          example: "3 × 4 ＝ ?",
          title: "九九の答えを珠に置く",
          desc: "3×4＝12を十の位・一の位に置く。",
          basic: "掛け算ではまず九九で答えを作ります。12なら十の位に1、一の位に2を置きます。",
          trouble: "『12』を一の位に12個置こうとしないこと。必ず十の位1、一の位2に分けます。",
          rule: "九九の答えも、位に分けて珠へ置く。",
          frames: [
            { value: 0, place: null, text: "3 × 4。九九で答えは12です。" },
            { value: 10, place: 10, text: "12の十の位、1を置きます。" },
            { value: 12, place: 1, text: "一の位に2を置いて12。" }
          ],
          quiz: { q: "3 × 4 の答え12。十の位に置く数は？", options: ["1", "2", "12"], answer: "1" }
        },
        {
          id: "mul2",
          example: "23 × 4 ＝ ?",
          title: "2桁×1桁の部分積",
          desc: "23×4を20×4と3×4に分ける。",
          basic: "23×4は『20×4＝80』と『3×4＝12』に分けます。部分積を位をそろえて足すと92です。",
          trouble: "2×4＝8を『8』として一の位へ置かないこと。2は十の位なので、20×4＝80です。",
          rule: "元の数字の位を、掛けた後も忘れない。",
          frames: [
            { value: 0, place: null, text: "23 × 4。まず20×4を考えます。" },
            { value: 80, place: 10, text: "20 × 4 ＝ 80。80を置きます。" },
            { value: 90, place: 10, text: "次に3×4＝12。まず十の位の10を加えます。" },
            { value: 92, place: 1, text: "一の位の2を加えて92。" }
          ],
          quiz: { q: "23 × 4 で、20 × 4 は？", options: ["8", "80", "800"], answer: "80" }
        },
        {
          id: "mul3",
          example: "24 × 13 ＝ ?",
          title: "2桁×2桁",
          desc: "24×13を×10と×3に分ける。",
          basic: "24×13は、24×10＝240と24×3＝72に分けます。240＋72＝312です。",
          trouble: "13を『1と3』として同じ位に扱わないこと。1は十の位なので×10です。",
          rule: "2桁の掛ける数は『十の位の部分積＋一の位の部分積』。",
          frames: [
            { value: 0, place: null, text: "24 × 13。13を10と3に分けます。" },
            { value: 240, place: 10, text: "24 × 10 ＝ 240。" },
            { value: 310, place: 10, text: "24 × 3 ＝72。まず70を加えます。" },
            { value: 312, place: 1, text: "残り2を加えて312。" }
          ],
          quiz: { q: "24 × 13 で、最初の大きな部分積は？", options: ["24×1", "24×10", "24×3"], answer: "24×10" }
        },
        {
          id: "mul4",
          example: "40 × 6 ＝ ?",
          title: "0がある数の位取り",
          desc: "40×6で、4ではなく40を掛けていると意識する。",
          basic: "40×6は4×6＝24を使いますが、4は十の位です。答えは24ではなく240です。",
          trouble: "途中の0を『何もしないから無視』して位まで消してしまうこと。0は珠を置かなくても、位は残ります。",
          rule: "0の珠は置かなくても、0が示す位は消えない。",
          frames: [
            { value: 0, place: null, text: "40 × 6。4×6＝24を使います。" },
            { value: 200, place: 100, text: "40の4は十の位。答え24も1桁左へずれて240。" },
            { value: 240, place: 10, text: "十の位に4を置いて240。" }
          ],
          quiz: { q: "40 × 6 の答えは？", options: ["24", "240", "2400"], answer: "240" }
        }
      ]
    },
    div: {
      symbol: "÷",
      title: "割り算",
      desc: "商を見つけ、位をそろえて答えの珠を置くところから始めます。",
      lessons: [
        {
          id: "div1",
          example: "8 ÷ 4 ＝ ?",
          title: "1桁÷1桁",
          desc: "8÷4＝2。何個分あるかを考える。",
          basic: "割り算は『4が8の中に何個あるか』を考えます。4×2＝8なので商は2です。",
          trouble: "引き算を何回も繰り返すだけにしないこと。九九の逆として『4×いくつ＝8』を使います。",
          rule: "割り算は九九を逆向きに使う。",
          frames: [
            { value: 0, place: null, text: "8 ÷ 4。4×いくつで8になるか考えます。" },
            { value: 2, place: 1, text: "4×2＝8なので、商は2。答えの珠に2を置きます。" }
          ],
          quiz: { q: "8 ÷ 4 は？", options: ["2", "3", "4"], answer: "2" }
        },
        {
          id: "div2",
          example: "84 ÷ 4 ＝ ?",
          title: "2桁÷1桁",
          desc: "84÷4を十の位から見る。",
          basic: "84÷4は、大きい位から見ます。8十÷4＝2十、次に4÷4＝1。答えは21です。",
          trouble: "84を一気に考えようとしないこと。十の位→一の位に分けます。",
          rule: "割り算も大きい位から商を決める。",
          frames: [
            { value: 0, place: null, text: "84 ÷ 4。まず8十を4で割ります。" },
            { value: 20, place: 10, text: "8十 ÷4＝2十。十の位へ2。" },
            { value: 21, place: 1, text: "残る4 ÷4＝1。一の位へ1。答え21。" }
          ],
          quiz: { q: "84 ÷ 4 の十の位の商は？", options: ["1", "2", "4"], answer: "2" }
        },
        {
          id: "div3",
          example: "156 ÷ 3 ＝ ?",
          title: "3桁÷1桁",
          desc: "156÷3を左から順に処理する。",
          basic: "156÷3は、15十÷3＝5十、6÷3＝2と見ます。商は52です。",
          trouble: "最初の1だけを3で割れないから止まらないこと。必要なら次の桁と合わせて15として見ます。",
          rule: "先頭の数字が割れなければ、次の桁までまとめる。",
          frames: [
            { value: 0, place: null, text: "156 ÷ 3。先頭の1だけでは3で割れません。" },
            { value: 50, place: 10, text: "15を3で割ると5。十の位に5を置きます。" },
            { value: 52, place: 1, text: "残る6を3で割ると2。答え52。" }
          ],
          quiz: { q: "156 ÷ 3 で、最初に『15』を見る理由は？", options: ["1だけでは3で割れない", "6があるから", "いつも15を見る"], answer: "1だけでは3で割れない" }
        },
        {
          id: "div4",
          example: "144 ÷ 12 ＝ ?",
          title: "2桁で割る",
          desc: "144÷12で商を見当づける。",
          basic: "2桁で割るときは『12×いくつなら今見ている数を超えないか』を考えます。144÷12は12です。",
          trouble: "大きすぎる商を置いてから戻す回数を減らすため、まず上の桁を使って商を見当づけます。",
          rule: "商は『割る数×商が、今の数を超えない最大』を探す。",
          frames: [
            { value: 0, place: null, text: "144 ÷ 12。まず商の十の位を考えます。" },
            { value: 10, place: 10, text: "12×10＝120。144を超えないので十の位に1。" },
            { value: 12, place: 1, text: "残り24には12が2個。商は12。" }
          ],
          quiz: { q: "144 ÷ 12 の商は？", options: ["10", "12", "14"], answer: "12" }
        }
      ]
    }
  };

  const learningVoiceScripts = {
    add1: [
      "2 たす 2 です。まず、2を おきます。",
      "1つ、たします。",
      "もう1つ たします。4です。"
    ],
    add2: [
      "5 たす 3 です。まず、5を おきます。",
      "1つ、たします。",
      "もう1つ、たします。",
      "もう1つ たして、8です。"
    ],
    add3: [
      "2 たす 4 です。まず、2を おきます。",
      "4は そのまま たせません。先に、1を ひきます。",
      "つぎに、5を たします。6です。"
    ],
    add4: [
      "8 たす 7 です。まず、8を おきます。",
      "10を またぎます。先に、3を ひきます。",
      "つぎに、10を たします。15です。"
    ],
    add5: [
      "36 たす 27 です。まず、36を おきます。",
      "先に、20を たします。56です。",
      "つぎは 7。先に、3を ひきます。",
      "さいごに、10を たします。63です。"
    ],
    sub1: [
      "4 ひく 2 です。まず、4を おきます。",
      "1つ、ひきます。",
      "もう1つ ひきます。2です。"
    ],
    sub2: [
      "8 ひく 3 です。まず、8を おきます。",
      "1つ、ひきます。",
      "もう1つ、ひきます。",
      "もう1つ ひいて、5です。"
    ],
    sub3: [
      "7 ひく 4 です。まず、7を おきます。",
      "4を そのまま ひけません。先に、5を ひきます。",
      "つぎに、1を たします。3です。"
    ],
    sub4: [
      "12 ひく 7 です。まず、12を おきます。",
      "7を そのまま ひけません。先に、10を ひきます。",
      "つぎに、3を たします。5です。"
    ],
    sub5: [
      "43 ひく 28 です。まず、43を おきます。",
      "先に、20を ひきます。23です。",
      "つぎは 8。先に、10を ひきます。",
      "さいごに、2を たします。15です。"
    ],
    mul1: [
      "3 かける 4 です。答えは 12です。",
      "まず、10のくらいに 1を おきます。",
      "1のくらいに 2を おきます。12です。"
    ],
    mul2: [
      "23 かける 4 です。20と3に わけます。",
      "20 かける 4 は、80です。",
      "3 かける 4 は、12。まず、10を たします。",
      "つぎに、2を たします。92です。"
    ],
    mul3: [
      "24 かける 13 です。10と3に わけます。",
      "24 かける 10 は、240です。",
      "24 かける 3 は、72。まず、70を たします。",
      "つぎに、2を たします。312です。"
    ],
    mul4: [
      "40 かける 6 です。4 かける 6 は、24です。",
      "40なので、くらいを 1つ ずらします。まず、200です。",
      "つぎに、40を おきます。240です。"
    ],
    div1: [
      "8 わる 4 です。4が いくつあるか、かんがえます。",
      "4 かける 2 は 8。だから、答えは 2です。"
    ],
    div2: [
      "84 わる 4 です。大きい くらいから みます。",
      "8じゅう わる 4 は、2じゅう。20を おきます。",
      "4 わる 4 は 1。答えは 21です。"
    ],
    div3: [
      "156 わる 3 です。左から みます。",
      "1だけでは われません。15 わる 3 は 5。50を おきます。",
      "6 わる 3 は 2。答えは 52です。"
    ],
    div4: [
      "144 わる 12 です。答えを だいたい かんがえます。",
      "12 かける 10 は 120。まず、10を おきます。",
      "のこりは 24。12が 2こ。答えは 12です。"
    ]
  };

  let learningProgress = loadTutorialProgress();
  let learningCurrentCategory = null;
  let learningCurrentLessonIndex = 0;
  let learningRods = [];
  let learningAnimationToken = 0;
  let learningAnimationPaused = false;
  let learningCurrentFrame = 0;
  let learningVoiceEnabled = false;
  let learningNarrationToken = 0;

  const BGM_SETTING_KEY = "soroban_bgm_enabled_v1";
  const VOICE_DEFAULT_SETTING_KEY = "soroban_voice_answer_default_v1";
  const LEARNING_VOICE_SETTING_KEY = "soroban_learning_voice_default_v1";
  const ANZAN_SPEED_SETTING_KEY = "soroban_anzan_speed_v1";
  const ANALYSIS_VIDEO_SETTING_KEY = "soroban_analysis_video_v1";

  let preferredVoiceAnswerEnabled = false;
  let preferredLearningVoiceEnabled = false;
  let anzanSpeed = "slow";
  let analysisVideoEnabled = false;

  let pendingVideoMode = null;
  let analysisCameraStream = null;
  let sessionMediaRecorder = null;
  let sessionVideoChunks = [];
  let sessionVideoBlob = null;
  let sessionVideoUrl = null;
  let sessionVideoMimeType = "";
  let sessionVideoStartedAtPerf = null;
  let sessionVideoStartedAtIso = null;
  let sessionVideoDurationMs = 0;
  let sessionVideoActive = false;
  let sessionVideoDiscardOnStop = false;
  let sessionVideoMode = null;
  let sessionQuestionMarks = [];
  let reviewClipEndSec = null;

  const ANZAN_TOTAL_QUESTIONS = 10;
  let anzanLevel = 1;
  let anzanQuestionIndex = 0;
  let anzanCorrect = 0;
  let anzanQuestion = null;
  let anzanAnswerBuffer = "";
  let anzanRunToken = 0;
  let anzanRecognition = null;
  let anzanListening = false;
  let anzanListeningStartedAt = 0;
  let anzanSpeechDetected = false;
  let anzanLocked = false;

  const RULES = Object.freeze({
    addSubOperandMin: 1,
    subtractionMinAnswer: 0,
    divisionMustBeInteger: true
  });

  const TOTAL_QUESTIONS = 12;

  // =====================================================
  // 自動レベルアップ・苦手克服
  // =====================================================
  const LEARNING_STORAGE_KEY = "soroban_learning_stats_v1";
  const RANKING_STORAGE_KEY = "soroban_score_rankings_v1";
  const CHALLENGE_COUNT_STORAGE_KEY = "soroban_challenge_counts_v1";
  const SESSION_HISTORY_STORAGE_KEY = "soroban_session_history_v1";
  const RANK_CERTIFICATION_STORAGE_KEY = "soroban_rank_certification_v2";
  const CHALLENGE_CURRICULUM_VERSION = "carry-borrow-split-v1";
  const MAX_SESSION_HISTORY = 200;
  const MAX_RANKING_ENTRIES_PER_MODE = 100;

  // 最初の5問（1〜5問目）には苦手克服問題を入れない。
  // その後の7問の中へ、最大5問を分散して入れる。
  const WEAKNESS_SLOTS = new Set([5, 6, 8, 9, 11]);

  // 苦手克服問題として1回正解した時点で克服扱い。
  // 一度克服した問題は、それ以降の苦手克服枠には出さない。
  const RECOVERY_STREAK_TO_MASTER = 1;

  // 同じ種類の学習内でのみ自動レベルアップする。
  const NEXT_MODE = Object.freeze({
    ba1: "ba2",
    ba2: "ba3",
    ba3: "ba4",
    ba4: "ba5",
    bs1: "bs2",
    bs2: "bs3",
    bs3: "bs4",
    bs4: "bs5",
    mm1: "mm2",
    mm2: "mm3",
    md1: "md2",
    md2: "md3",
    a2: "a3"
  });

  let currentMode = null;
  let questions = [];
  let index = 0;
  let score = 0;
  let answers = [];
  let seconds = 300;
  let timerHandle = null;
  let timeLimitEnabled = true;
  let locked = false;
  let correctStreak = 0;
  let maxCorrectStreak = 0;
  let scoreAnimationToken = 0;
  let bgmOn = true;
  let currentBgm = null;
  let currentBgmGroupKey = null;

  let learningStats = loadLearningStats();
  let scoreRankings = loadScoreRankings();
  let challengeCounts = loadChallengeCounts();
  let sessionHistory = loadSessionHistory();
  let sessionWeakAsked = 0;
  let sessionWeakCorrect = 0;
  let promotedMode = null;
  let sessionRankingSaved = false;
  let sessionChallengeRecorded = false;
  let sessionHistorySaved = false;
  let activeRemedialSkillId = null;
  let questionTimingLog = [];
  let rankingModalMode = null;

  let inAppBackGuardReady = false;
  let handlingBrowserBack = false;

  let hintRunToken = 0;
  let hintDigitCount = 1;
  let hintRods = [];
  let hintManualSteps = [];
  let hintManualIndex = 0;

  let voiceAnswerEnabled = false;
  let voiceRecognition = null;
  let voiceRecognitionRunning = false;
  let voicePauseForFeedback = false;
  let voiceCalloutRunning = false;
  let voiceRestartTimer = null;
  let voiceListeningStartedAt = 0;
  let voiceSpeechDetected = false;

  // 「ゴワサン」は1問につき1回だけ。
  // 音声認識が無音で切れても、この番号が同じ間は言い直さない。
  let gowasanSpokenForIndex = -1;

  // 珠ヒントの音声解説中は、回答用マイクを止める。
  let hintNarrationActive = false;
  let hintNarrationToken = 0;

  // 「ゴワサン！」の直後だけ短く余韻を待つ。
  // 問題を解いている間はBGMを止めない。
  const MIC_PRE_SILENCE_MS = 450;
  const MIC_RESULT_GUARD_MS = 350;

  const BGM_VOLUME_NORMAL = 0.18;
  const BGM_VOLUME_APP_SPEECH = 0.03;
  const BGM_VOLUME_LISTENING = 0.00;

  let bgmKeepAliveTimer = null;

  class BgmEngine {
    constructor(urls) {
      this.urls = urls;
      this.ctx = null;
      this.masterGain = null;
      this.buffers = new Map();

      this.currentName = null;
      this.currentSource = null;
      this.currentSourceGain = null;

      this.playGeneration = 0;
      this.targetVolume = BGM_VOLUME_NORMAL;
    }

    ensureContext() {
      const AudioContextClass =
        window.AudioContext ||
        window.webkitAudioContext;

      if (!AudioContextClass) {
        return false;
      }

      if (!this.ctx) {
        this.ctx = new AudioContextClass({
          latencyHint: "interactive"
        });

        this.masterGain =
          this.ctx.createGain();

        this.masterGain.gain.value =
          this.targetVolume;

        this.masterGain.connect(
          this.ctx.destination
        );
      }

      // resume() は非同期だが、ここでは呼び出し自体を
      // ユーザー操作と同じイベント内で開始する。
      if (this.ctx.state !== "running") {
        this.ctx.resume().catch(() => {});
      }

      return true;
    }

    async ensureRunning() {
      if (!this.ensureContext()) {
        return false;
      }

      if (this.ctx.state !== "running") {
        try {
          await this.ctx.resume();
        } catch (error) {
          return false;
        }
      }

      return this.ctx.state === "running";
    }

    setVolume(value, rampSeconds = 0.12) {
      this.targetVolume = value;

      if (
        !this.ctx ||
        !this.masterGain
      ) {
        return;
      }

      const now = this.ctx.currentTime;
      const gain = this.masterGain.gain;

      gain.cancelScheduledValues(now);
      gain.setValueAtTime(
        Math.max(0.0001, gain.value),
        now
      );
      gain.linearRampToValueAtTime(
        value,
        now + rampSeconds
      );
    }

    async loadBuffer(name) {
      if (this.buffers.has(name)) {
        return this.buffers.get(name);
      }

      const url = this.urls[name];

      if (!url) {
        throw new Error(
          `BGM URLがありません: ${name}`
        );
      }

      const ready =
        await this.ensureRunning();

      if (!ready) {
        throw new Error(
          "Web Audioを開始できません。"
        );
      }

      const response = await fetch(
        url,
        {
          method: "GET",
          cache: "force-cache",
          mode: "cors"
        }
      );

      if (!response.ok) {
        throw new Error(
          `BGM取得失敗: ${response.status}`
        );
      }

      const arrayBuffer =
        await response.arrayBuffer();

      const decoded =
        await this.ctx.decodeAudioData(
          arrayBuffer.slice(0)
        );

      this.buffers.set(name, decoded);
      return decoded;
    }

    async play(name, force = false) {
      if (!name) {
        return false;
      }

      this.ensureContext();

      if (
        !force &&
        this.currentName === name &&
        this.currentSource
      ) {
        await this.ensureRunning();
        return true;
      }

      const generation =
        ++this.playGeneration;

      let buffer;

      try {
        buffer =
          await this.loadBuffer(name);
      } catch (error) {
        console.error(
          "BGM load error",
          error
        );
        return false;
      }

      if (
        generation !==
        this.playGeneration
      ) {
        return false;
      }

      const ready =
        await this.ensureRunning();

      if (!ready) {
        return false;
      }

      const newSource =
        this.ctx.createBufferSource();

      const newSourceGain =
        this.ctx.createGain();

      newSource.buffer = buffer;
      newSource.loop = true;

      newSourceGain.gain.value = 0.0001;

      newSource.connect(newSourceGain);
      newSourceGain.connect(
        this.masterGain
      );

      const oldSource =
        this.currentSource;

      const oldSourceGain =
        this.currentSourceGain;

      const now = this.ctx.currentTime;

      newSource.start(0);

      newSourceGain.gain.setValueAtTime(
        0.0001,
        now
      );

      newSourceGain.gain.linearRampToValueAtTime(
        1.0,
        now + 0.18
      );

      this.currentName = name;
      this.currentSource = newSource;
      this.currentSourceGain =
        newSourceGain;

      if (
        oldSource &&
        oldSourceGain
      ) {
        try {
          oldSourceGain.gain
            .cancelScheduledValues(now);

          oldSourceGain.gain
            .setValueAtTime(
              Math.max(
                0.0001,
                oldSourceGain.gain.value
              ),
              now
            );

          oldSourceGain.gain
            .linearRampToValueAtTime(
              0.0001,
              now + 0.18
            );

          setTimeout(() => {
            try {
              oldSource.stop();
            } catch (error) {}
            try {
              oldSource.disconnect();
            } catch (error) {}
            try {
              oldSourceGain.disconnect();
            } catch (error) {}
          }, 230);
        } catch (error) {}
      }

      return true;
    }

    async keepAlive() {
      const ready =
        await this.ensureRunning();

      if (!ready) {
        return false;
      }

      // マイク開始時にブラウザがAudioContextを
      // 一時停止しても、ここでは同じ再生ノードを維持する。
      // BGMのpause/startは行わない。
      return true;
    }

    stop() {
      ++this.playGeneration;

      if (this.currentSource) {
        try {
          this.currentSource.stop();
        } catch (error) {}

        try {
          this.currentSource.disconnect();
        } catch (error) {}
      }

      if (this.currentSourceGain) {
        try {
          this.currentSourceGain.disconnect();
        } catch (error) {}
      }

      this.currentSource = null;
      this.currentSourceGain = null;
      this.currentName = null;
    }

    hasActiveSource() {
      return Boolean(
        this.currentSource &&
        this.currentName
      );
    }
  }

  const bgmEngine =
    new BgmEngine(BGM_URLS);

  const randInt = (min, max) =>
    Math.floor(Math.random() * (max - min + 1)) + min;

  // 暗算モードの旧呼び出し名も同じ実装へ統一。
  const randomInt = randInt;

  const coin = () => Math.random() < 0.5;

  function calculate(a, op, b) {
    if (op === "＋") return a + b;
    if (op === "－") return a - b;
    if (op === "×") return a * b;
    return a / b;
  }

  function isValidQuestion(q) {
    if (!q) return false;

    if (q.op === "＋" || q.op === "－") {
      if (q.a < RULES.addSubOperandMin) return false;
      if (q.b < RULES.addSubOperandMin) return false;
    }

    if (q.op === "－" && q.answer < RULES.subtractionMinAnswer) {
      return false;
    }

    if (q.op === "÷") {
      if (q.b <= 0) return false;
      if (RULES.divisionMustBeInteger && !Number.isInteger(q.answer)) {
        return false;
      }
    }

    return true;
  }

  function makeQuestion(rawA, op, rawB) {
    // 割り算以外は、計算式確定直前に左右へ必ず+1。
    const a = op === "÷" ? rawA : rawA + 1;
    const b = op === "÷" ? rawB : rawB + 1;

    const q = {
      a,
      op,
      b,
      answer: calculate(a, op, b)
    };

    if (!isValidQuestion(q)) {
      throw new Error(
        `禁止問題: raw=${rawA} ${op} ${rawB}, final=${a} ${op} ${b}`
      );
    }

    return q;
  }

  function makeAddSub(finalMin, finalMax) {
    const rawMin = Math.max(0, finalMin - 1);
    const rawMax = finalMax - 1;
    const op = coin() ? "＋" : "－";

    if (op === "＋") {
      return makeQuestion(
        randInt(rawMin, rawMax),
        "＋",
        randInt(rawMin, rawMax)
      );
    }

    const rawA = randInt(rawMin, rawMax);
    const rawB = randInt(rawMin, rawA);
    return makeQuestion(rawA, "－", rawB);
  }

  function makeAdditionOnly(finalMin, finalMax) {
    const rawMin = Math.max(0, finalMin - 1);
    const rawMax = finalMax - 1;

    return makeQuestion(
      randInt(rawMin, rawMax),
      "＋",
      randInt(rawMin, rawMax)
    );
  }

  function makeSubtractionOnly(finalMin, finalMax) {
    const rawMin = Math.max(0, finalMin - 1);
    const rawMax = finalMax - 1;

    // rawA >= rawB としておき、最終+1後も大小関係を維持する。
    const rawA = randInt(rawMin, rawMax);
    const rawB = randInt(rawMin, rawA);

    return makeQuestion(rawA, "－", rawB);
  }


  function onesAdditionCarries(a, b) {
    return (
      (Math.abs(a) % 10) +
      (Math.abs(b) % 10)
    ) >= 10;
  }

  function onesSubtractionBorrows(a, b) {
    return (
      Math.abs(a) % 10
    ) < (
      Math.abs(b) % 10
    );
  }

  function makeAdditionByOnesCarry(
    digitLevel,
    carryRequired
  ) {
    for (
      let tries = 0;
      tries < 2000;
      tries += 1
    ) {
      const min =
        digitLevel === 1
          ? 1
          : 10;

      const max =
        digitLevel === 1
          ? 9
          : 99;

      const a = randInt(min, max);
      const b = randInt(min, max);

      if (
        digitLevel === 2 &&
        a + b > 99
      ) {
        continue;
      }

      if (
        onesAdditionCarries(a, b) !==
        Boolean(carryRequired)
      ) {
        continue;
      }

      // 非除算は従来どおり raw 値から +1 して確定する。
      return makeQuestion(
        a - 1,
        "＋",
        b - 1
      );
    }

    throw new Error(
      `繰り上がり条件の足し算を生成できません: level=${digitLevel}, carry=${carryRequired}`
    );
  }

  function makeSubtractionByOnesBorrow(
    digitLevel,
    borrowRequired
  ) {
    for (
      let tries = 0;
      tries < 2000;
      tries += 1
    ) {
      let a;
      let b;

      if (
        digitLevel === 1 &&
        borrowRequired
      ) {
        // 例: 12-7。引く数は1桁のまま、十の位から必ず繰り下がる。
        a = randInt(10, 18);
        b = randInt(1, 9);
      } else if (
        digitLevel === 1
      ) {
        a = randInt(1, 9);
        b = randInt(1, a);
      } else {
        a = randInt(10, 99);
        b = randInt(10, a);
      }

      if (a < b) {
        continue;
      }

      if (
        onesSubtractionBorrows(a, b) !==
        Boolean(borrowRequired)
      ) {
        continue;
      }

      return makeQuestion(
        a - 1,
        "－",
        b - 1
      );
    }

    throw new Error(
      `繰り下がり条件の引き算を生成できません: level=${digitLevel}, borrow=${borrowRequired}`
    );
  }

  function questionMatchesMode(
    mode,
    q
  ) {
    if (!q) {
      return false;
    }

    const a = Number(q.a);
    const b = Number(q.b);

    if (
      !Number.isFinite(a) ||
      !Number.isFinite(b)
    ) {
      return false;
    }

    if (mode === "ba1") {
      return (
        q.op === "＋" &&
        a >= 1 && a <= 9 &&
        b >= 1 && b <= 9 &&
        !onesAdditionCarries(a, b)
      );
    }

    if (mode === "ba2") {
      return (
        q.op === "＋" &&
        a >= 1 && a <= 9 &&
        b >= 1 && b <= 9 &&
        onesAdditionCarries(a, b)
      );
    }

    if (mode === "ba3") {
      return (
        q.op === "＋" &&
        a >= 10 && a <= 99 &&
        b >= 10 && b <= 99 &&
        a + b <= 99 &&
        !onesAdditionCarries(a, b)
      );
    }

    if (mode === "ba4") {
      return (
        q.op === "＋" &&
        a >= 10 && a <= 99 &&
        b >= 10 && b <= 99 &&
        a + b <= 99 &&
        onesAdditionCarries(a, b)
      );
    }

    if (mode === "ba5") {
      return (
        q.op === "＋" &&
        a >= 100 && a <= 999 &&
        b >= 100 && b <= 999
      );
    }

    if (mode === "bs1") {
      return (
        q.op === "－" &&
        a >= 1 && a <= 9 &&
        b >= 1 && b <= 9 &&
        a >= b &&
        !onesSubtractionBorrows(a, b)
      );
    }

    if (mode === "bs2") {
      return (
        q.op === "－" &&
        a >= 10 && a <= 18 &&
        b >= 1 && b <= 9 &&
        a >= b &&
        onesSubtractionBorrows(a, b)
      );
    }

    if (mode === "bs3") {
      return (
        q.op === "－" &&
        a >= 10 && a <= 99 &&
        b >= 10 && b <= 99 &&
        a >= b &&
        !onesSubtractionBorrows(a, b)
      );
    }

    if (mode === "bs4") {
      return (
        q.op === "－" &&
        a >= 10 && a <= 99 &&
        b >= 10 && b <= 99 &&
        a >= b &&
        onesSubtractionBorrows(a, b)
      );
    }

    if (mode === "bs5") {
      return (
        q.op === "－" &&
        a >= 100 && a <= 999 &&
        b >= 100 && b <= 999 &&
        a >= b
      );
    }

    return true;
  }

  function makeMul(finalAMin, finalAMax, finalBMin, finalBMax) {
    const rawAMin = Math.max(0, finalAMin - 1);
    const rawAMax = finalAMax - 1;
    const rawBMin = Math.max(0, finalBMin - 1);
    const rawBMax = finalBMax - 1;

    return makeQuestion(
      randInt(rawAMin, rawAMax),
      "×",
      randInt(rawBMin, rawBMax)
    );
  }

  function makeExactDivision(numeratorMin, numeratorMax, divisorMin, divisorMax) {
    const divisorStart = Math.max(1, divisorMin);

    for (let tries = 0; tries < 1000; tries += 1) {
      const divisor = randInt(divisorStart, divisorMax);
      const quotientMin = Math.max(1, Math.ceil(numeratorMin / divisor));
      const quotientMax = Math.floor(numeratorMax / divisor);

      if (quotientMin <= quotientMax) {
        const quotient = randInt(quotientMin, quotientMax);
        return makeQuestion(divisor * quotient, "÷", divisor);
      }
    }

    throw new Error("整数になる割り算を生成できませんでした。");
  }

  function loadChallengeCounts() {
    try {
      const raw =
        localStorage.getItem(
          CHALLENGE_COUNT_STORAGE_KEY
        );

      if (!raw) {
        return {};
      }

      const parsed =
        JSON.parse(raw);

      if (
        !parsed ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        return {};
      }

      return parsed;
    } catch (error) {
      return {};
    }
  }

  function saveChallengeCounts() {
    try {
      localStorage.setItem(
        CHALLENGE_COUNT_STORAGE_KEY,
        JSON.stringify(
          challengeCounts
        )
      );
    } catch (error) {
      // 保存できなくてもゲーム自体は継続する。
    }
  }

  function getChallengeCount(mode) {
    return Math.max(
      0,
      Math.floor(
        Number(
          challengeCounts[mode]
        ) || 0
      )
    );
  }

  function incrementChallengeCount(mode) {
    const next =
      getChallengeCount(mode) + 1;

    challengeCounts[mode] = next;
    saveChallengeCounts();

    updateModeChallengeCountLabels();

    return next;
  }

  function modeVoiceName(mode) {
    const title =
      modeInfo[mode]?.title ||
      mode;

    return String(title)
      .split("｜")[0];
  }

  function updateModeChallengeCountLabels() {
    root
      .querySelectorAll(
        ".mode-challenge-count"
      )
      .forEach((node) => {
        const mode =
          node.dataset.mode;

        if (!mode) {
          return;
        }

        node.textContent =
          `総チャレンジ ${getChallengeCount(mode)}回`;
      });
  }

  function setupModeChallengeCountLabels() {
    root
      .querySelectorAll(
        ".start-mode"
      )
      .forEach((startButton) => {
        const mode =
          startButton.dataset.mode;

        if (!mode) {
          return;
        }

        const card =
          startButton.closest(
            ".mode-card"
          );

        if (!card) {
          return;
        }

        if (
          card.querySelector(
            `.mode-challenge-count[data-mode="${mode}"]`
          )
        ) {
          return;
        }

        const label =
          document.createElement(
            "div"
          );

        label.className =
          "mode-challenge-count";

        label.dataset.mode =
          mode;

        startButton.insertAdjacentElement(
          "beforebegin",
          label
        );
      });

    updateModeChallengeCountLabels();
  }

  function loadScoreRankings() {
    try {
      const raw =
        localStorage.getItem(
          RANKING_STORAGE_KEY
        );

      if (!raw) {
        return { modes: {} };
      }

      const parsed =
        JSON.parse(raw);

      if (
        !parsed ||
        typeof parsed !== "object"
      ) {
        return { modes: {} };
      }

      if (
        !parsed.modes ||
        typeof parsed.modes !== "object"
      ) {
        parsed.modes = {};
      }

      return parsed;
    } catch (error) {
      return { modes: {} };
    }
  }

  function saveScoreRankings() {
    try {
      localStorage.setItem(
        RANKING_STORAGE_KEY,
        JSON.stringify(
          scoreRankings
        )
      );
    } catch (error) {
      // 保存できなくてもゲーム自体は継続する。
    }
  }

  function rankingEntriesForMode(mode) {
    const entries =
      scoreRankings.modes[mode];

    if (!Array.isArray(entries)) {
      return [];
    }

    return [...entries].sort(
      (a, b) =>
        (b.score - a.score) ||
        (b.correctCount - a.correctCount) ||
        (b.maxStreak - a.maxStreak) ||
        (b.remainingSeconds - a.remainingSeconds) ||
        (a.playedAt - b.playedAt)
    );
  }

  function addScoreRanking(
    mode,
    scoreInfo,
    timeup
  ) {
    if (!mode || !scoreInfo) {
      return;
    }

    if (
      !scoreRankings.modes[mode] ||
      !Array.isArray(
        scoreRankings.modes[mode]
      )
    ) {
      scoreRankings.modes[mode] = [];
    }

    const entry = {
      id:
        `${Date.now()}-` +
        Math.random()
          .toString(36)
          .slice(2, 9),
      score:
        Number(scoreInfo.finalScore) || 0,
      correctCount:
        Number(scoreInfo.correctCount) || 0,
      maxStreak:
        Number(scoreInfo.maxStreak) || 0,
      remainingSeconds:
        Number(scoreInfo.remainingSeconds) || 0,
      wrongCount:
        Number(scoreInfo.wrongCount) || 0,
      timeup:
        Boolean(timeup),
      playedAt:
        Date.now()
    };

    scoreRankings.modes[mode].push(
      entry
    );

    scoreRankings.modes[mode] =
      rankingEntriesForMode(mode)
        .slice(
          0,
          MAX_RANKING_ENTRIES_PER_MODE
        );

    saveScoreRankings();
  }

  function deleteRankingEntry(
    mode,
    entryId
  ) {
    const entries =
      scoreRankings.modes[mode];

    if (!Array.isArray(entries)) {
      return;
    }

    scoreRankings.modes[mode] =
      entries.filter(
        (entry) =>
          entry.id !== entryId
      );

    saveScoreRankings();

    if (currentMode === mode) {
      renderResultRanking(mode);
    }

    if (rankingModalMode === mode) {
      renderRankingList(
        rankingModalList,
        mode
      );
    }
  }

  function formatRankingTime(
    secondsValue
  ) {
    const safe =
      Math.max(
        0,
        Math.floor(
          Number(secondsValue) || 0
        )
      );

    const minutes =
      Math.floor(safe / 60);

    const secondsPart =
      safe % 60;

    return (
      `${minutes}:` +
      String(secondsPart)
        .padStart(2, "0")
    );
  }

  function formatRankingDate(
    timestamp
  ) {
    try {
      return new Date(timestamp)
        .toLocaleString(
          "ja-JP",
          {
            month: "numeric",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit"
          }
        );
    } catch (error) {
      return "";
    }
  }

  function renderRankingList(
    container,
    mode
  ) {
    if (!container) {
      return;
    }

    container.innerHTML = "";

    const entries =
      rankingEntriesForMode(mode);

    if (entries.length === 0) {
      const empty =
        document.createElement(
          "div"
        );

      empty.className =
        "ranking-empty";

      empty.textContent =
        "まだ得点記録がありません。";

      container.appendChild(empty);
      return;
    }

    entries.forEach(
      (entry, index) => {
        const row =
          document.createElement(
            "div"
          );

        row.className =
          "ranking-row";

        const place =
          document.createElement(
            "div"
          );

        place.className =
          "ranking-place";

        place.textContent =
          `${index + 1}`;

        const main =
          document.createElement(
            "div"
          );

        main.className =
          "ranking-main";

        const scoreEl =
          document.createElement(
            "div"
          );

        scoreEl.className =
          "ranking-score";

        scoreEl.textContent =
          `${entry.score.toLocaleString()}点`;

        const detail =
          document.createElement(
            "div"
          );

        detail.className =
          "ranking-detail";

        detail.textContent =
          `${entry.correctCount}/${TOTAL_QUESTIONS}正解` +
          ` ・ 最高${entry.maxStreak}連続` +
          ` ・ 残り${formatRankingTime(entry.remainingSeconds)}` +
          ` ・ ${formatRankingDate(entry.playedAt)}`;

        const deleteBtn =
          document.createElement(
            "button"
          );

        deleteBtn.type =
          "button";

        deleteBtn.className =
          "ranking-delete";

        deleteBtn.textContent =
          "削除";

        deleteBtn.addEventListener(
          "click",
          () => {
            const ok =
              window.confirm(
                "この得点記録を削除しますか？"
              );

            if (!ok) {
              return;
            }

            deleteRankingEntry(
              mode,
              entry.id
            );
          }
        );

        main.appendChild(scoreEl);
        main.appendChild(detail);

        row.appendChild(place);
        row.appendChild(main);
        row.appendChild(deleteBtn);

        container.appendChild(row);
      }
    );
  }

  function renderResultRanking(mode) {
    renderRankingList(
      resultRankingList,
      mode
    );
  }

  function openRankingModal(mode) {
    rankingModalMode = mode;

    rankingModalTitle.textContent =
      `${modeInfo[mode].title}｜得点ランキング`;

    renderRankingList(
      rankingModalList,
      mode
    );

    rankingModal.classList.add(
      "show"
    );

    rankingModal.setAttribute(
      "aria-hidden",
      "false"
    );
  }

  function closeRankingModal() {
    rankingModalMode = null;

    rankingModal.classList.remove(
      "show"
    );

    rankingModal.setAttribute(
      "aria-hidden",
      "true"
    );
  }

  function setupModeRankingButtons() {
    root
      .querySelectorAll(
        ".start-mode"
      )
      .forEach((startButton) => {
        const mode =
          startButton.dataset.mode;

        if (!mode) {
          return;
        }

        const button =
          document.createElement(
            "button"
          );

        button.type =
          "button";

        button.className =
          "ranking-open-btn";

        button.dataset.mode =
          mode;

        button.textContent =
          "過去の得点ランキング（5分）";

        button.addEventListener(
          "click",
          () => {
            openRankingModal(mode);
          }
        );

        startButton.insertAdjacentElement(
          "afterend",
          button
        );
      });
  }

  const SKILL_DEFS = Object.freeze({
    add_direct: {
      title: "足し算の基本操作",
      shortIssue: "足せる珠をそのまま動かす",
      explanation: "同じ位に使える一珠があるときは、その珠をそのまま梁へ寄せます。まずは1〜4の珠の形を見て判断します。",
      category: "add",
      lessonIndex: 0,
      baseMode: "ba1",
      weight: 1.0
    },
    add_five: {
      title: "5の補数",
      shortIssue: "5の補数をすぐ出す",
      explanation: "5をまたぐ足し算では、足したい数をそのまま足せないときに5の補数を使います。たとえば＋4は、先に−1してから＋5です。",
      category: "add",
      lessonIndex: 2,
      baseMode: "ba1",
      weight: 1.2
    },
    add_ten: {
      title: "10の補数・繰り上がり",
      shortIssue: "10の補数をすぐ出す",
      explanation: "10をまたぐ足し算では、先に一の位から10の補数を引き、そのあと1つ上の位へ1を足します。8＋7なら−3→＋10です。",
      category: "add",
      lessonIndex: 3,
      baseMode: "ba2",
      weight: 1.5
    },
    add_multi: {
      title: "複数桁の足し算",
      shortIssue: "位ごとに分けて足す",
      explanation: "2桁以上は大きい位から順に分けます。十の位を処理してから一の位を処理すると、繰り上がりの場所が分かりやすくなります。",
      category: "add",
      lessonIndex: 4,
      baseMode: "ba3",
      weight: 1.7
    },
    sub_direct: {
      title: "引き算の基本操作",
      shortIssue: "取れる珠をそのまま取る",
      explanation: "引きたい数だけ珠があるときは、その珠を梁から離します。答えを新しく作るのではなく、今ある珠から取ります。",
      category: "sub",
      lessonIndex: 0,
      baseMode: "bs1",
      weight: 1.0
    },
    sub_five: {
      title: "5の補数で引く",
      shortIssue: "5の補数で引く",
      explanation: "一珠だけでは引けないときは、先に−5して補数を足し戻します。7−4なら−5→＋1です。",
      category: "sub",
      lessonIndex: 2,
      baseMode: "bs1",
      weight: 1.2
    },
    sub_ten: {
      title: "10の補数・繰り下がり",
      shortIssue: "繰り下がりを安定させる",
      explanation: "一の位だけで引けないときは、先に1つ上の位から1を引き、そのあと10の補数を一の位へ足し戻します。",
      category: "sub",
      lessonIndex: 3,
      baseMode: "bs2",
      weight: 1.5
    },
    sub_multi: {
      title: "複数桁の引き算",
      shortIssue: "位ごとに分けて引く",
      explanation: "2桁以上は大きい位から順に引きます。下の位で足りないときだけ、繰り下がりを使います。",
      category: "sub",
      lessonIndex: 4,
      baseMode: "bs3",
      weight: 1.7
    },
    mul_basic: {
      title: "九九",
      shortIssue: "九九を安定させる",
      explanation: "まず掛け算の答えを確実に出し、そのあと答えを十の位・一の位へ分けて珠に置きます。",
      category: "mul",
      lessonIndex: 0,
      baseMode: "mm1",
      weight: 1.7
    },
    mul_place: {
      title: "掛け算の位取り",
      shortIssue: "部分積の位をそろえる",
      explanation: "2桁以上の掛け算では、十の位と一の位の部分積を分け、元の数字の位を保って足し合わせます。",
      category: "mul",
      lessonIndex: 1,
      baseMode: "mm2",
      weight: 2.1
    },
    div_basic: {
      title: "割り算の基本",
      shortIssue: "割り算の答えを安定させる",
      explanation: "割り算は、割る数を何倍すると元の数になるかを考えます。まず小さい整数の割り算を安定させます。",
      category: "div",
      lessonIndex: 0,
      baseMode: "md1",
      weight: 1.8
    },
    div_place: {
      title: "割り算の位取り",
      shortIssue: "大きい数の割り算を位で考える",
      explanation: "大きい数の割り算では、上の位から商を考え、答えを正しい位へ置きます。",
      category: "div",
      lessonIndex: 1,
      baseMode: "md2",
      weight: 2.2
    }
  });

  function loadSessionHistory() {
    try {
      const raw = localStorage.getItem(
        SESSION_HISTORY_STORAGE_KEY
      );

      if (!raw) {
        return [];
      }

      const parsed = JSON.parse(raw);
      return Array.isArray(parsed)
        ? parsed.slice(-MAX_SESSION_HISTORY)
        : [];
    } catch (error) {
      return [];
    }
  }

  function saveSessionHistory() {
    try {
      sessionHistory = sessionHistory
        .slice(-MAX_SESSION_HISTORY);

      localStorage.setItem(
        SESSION_HISTORY_STORAGE_KEY,
        JSON.stringify(sessionHistory)
      );
    } catch (error) {}
  }

  function digitLength(value) {
    return String(
      Math.abs(
        Math.floor(Number(value) || 0)
      )
    ).length;
  }

  function hasAdditionCarry(a, b) {
    let x = Math.floor(Math.abs(a));
    let y = Math.floor(Math.abs(b));
    let carry = 0;

    while (x > 0 || y > 0 || carry > 0) {
      const dx = x % 10;
      const dy = y % 10;
      const sum = dx + dy + carry;

      if (sum >= 10) {
        return true;
      }

      carry = sum >= 10 ? 1 : 0;
      x = Math.floor(x / 10);
      y = Math.floor(y / 10);
    }

    return false;
  }

  function hasSubtractionBorrow(a, b) {
    let x = Math.floor(Math.abs(a));
    let y = Math.floor(Math.abs(b));
    let borrow = 0;

    while (x > 0 || y > 0) {
      let dx = (x % 10) - borrow;
      const dy = y % 10;

      if (dx < dy) {
        return true;
      }

      borrow = dx < dy ? 1 : 0;
      x = Math.floor(x / 10);
      y = Math.floor(y / 10);
    }

    return false;
  }

  function skillForQuestion(q) {
    if (!q) {
      return "add_direct";
    }

    if (q.op === "＋") {
      if (
        digitLength(q.a) > 1 ||
        digitLength(q.b) > 1
      ) {
        return "add_multi";
      }

      if (q.a + q.b >= 10) {
        return "add_ten";
      }

      if (
        q.a < 5 &&
        q.b < 5 &&
        q.a + q.b >= 5
      ) {
        return "add_five";
      }

      return "add_direct";
    }

    if (q.op === "－") {
      if (
        digitLength(q.a) === 2 &&
        digitLength(q.b) === 1 &&
        onesSubtractionBorrows(q.a, q.b)
      ) {
        return "sub_ten";
      }

      if (
        digitLength(q.a) > 1 ||
        digitLength(q.b) > 1
      ) {
        return hasSubtractionBorrow(
          q.a,
          q.b
        )
          ? "sub_multi"
          : "sub_direct";
      }

      if (
        q.a >= 5 &&
        q.b < 5 &&
        (q.a % 5) < q.b
      ) {
        return "sub_five";
      }

      return "sub_direct";
    }

    if (q.op === "×") {
      return (
        q.a <= 9 &&
        q.b <= 9
      )
        ? "mul_basic"
        : "mul_place";
    }

    if (q.op === "÷") {
      return (
        q.a <= 81 &&
        q.b <= 9
      )
        ? "div_basic"
        : "div_place";
    }

    return "add_direct";
  }

  function collectSkillEvidence() {
    const evidence = {};

    Object.keys(SKILL_DEFS).forEach(
      (skillId) => {
        evidence[skillId] = {
          attempts: 0,
          correct: 0,
          hints: 0
        };
      }
    );

    Object.values(
      learningStats.problems || {}
    ).forEach((stat) => {
      const skillId =
        skillForQuestion(stat);

      if (!evidence[skillId]) {
        return;
      }

      evidence[skillId].attempts +=
        Number(stat.attempts) || 0;

      evidence[skillId].correct +=
        Number(stat.correct) || 0;
    });

    sessionHistory.forEach((session) => {
      (session.questions || [])
        .forEach((question) => {
          const skillId =
            question.skillId ||
            skillForQuestion(question);

          if (
            evidence[skillId] &&
            question.hintUsed
          ) {
            evidence[skillId].hints += 1;
          }
        });
    });

    return evidence;
  }

  const APP_RANK_CERTIFICATIONS =
    Object.freeze([
      {
        name: "10級",
        type: "any",
        modes: ["ba1", "bs1"],
        minCorrect: 10,
        requirement:
          "繰り上がり・繰り下がりなしの1桁レベルで10/12以上"
      },
      {
        name: "9級",
        type: "all",
        modes: ["ba1", "bs1"],
        minCorrect: 10,
        requirement:
          "ADD1・SUB1を両方10/12以上"
      },
      {
        name: "8級",
        type: "all",
        modes: ["ba2", "bs2"],
        minCorrect: 10,
        requirement:
          "1桁レベルの繰り上がり・繰り下がりを両方10/12以上"
      },
      {
        name: "7級",
        type: "all",
        modes: ["ba3", "bs3"],
        minCorrect: 10,
        requirement:
          "2桁の繰り上がり・繰り下がりなしを両方10/12以上"
      },
      {
        name: "6級",
        type: "all",
        modes: ["ba4", "bs4"],
        minCorrect: 10,
        requirement:
          "2桁の繰り上がり・繰り下がりありを両方10/12以上"
      },
      {
        name: "5級",
        type: "all",
        modes: ["ba5", "bs5"],
        minCorrect: 10,
        requirement:
          "3桁の足し算・引き算を両方10/12以上"
      },
      {
        name: "4級",
        type: "all",
        modes: ["mm1", "md1"],
        minCorrect: 10,
        requirement:
          "1桁の掛け算・割り算を両方10/12以上"
      },
      {
        name: "3級",
        type: "all",
        modes: ["mm2", "md2"],
        minCorrect: 10,
        requirement:
          "中級2の掛け算・割り算を両方10/12以上"
      },
      {
        name: "2級",
        type: "all",
        modes: ["mm3", "md3"],
        minCorrect: 10,
        requirement:
          "中級3の掛け算・割り算を両方10/12以上"
      },
      {
        name: "1級",
        type: "all",
        modes: ["mm3", "md3"],
        minCorrect: 11,
        requirement:
          "中級3の掛け算・割り算を両方11/12以上"
      },
      {
        name: "初段",
        type: "any",
        modes: ["a1"],
        minCorrect: 11,
        requirement:
          "上級1（4桁の足し算・引き算）で11/12以上"
      },
      {
        name: "二段",
        type: "any",
        modes: ["a1"],
        minCorrect: 12,
        requirement:
          "上級1（4桁の足し算・引き算）で12/12"
      },
      {
        name: "三段",
        type: "any",
        modes: ["a2"],
        minCorrect: 11,
        requirement:
          "上級2（3桁×2桁 / 4桁÷2桁）で11/12以上"
      },
      {
        name: "名人",
        type: "any",
        modes: ["a3"],
        minCorrect: 12,
        requirement:
          "上級3（3桁×3桁 / 5桁÷3桁）で12/12"
      }
    ]);

  function loadRankCertificationState() {
    try {
      const raw =
        localStorage.getItem(
          RANK_CERTIFICATION_STORAGE_KEY
        );

      if (!raw) {
        return {
          bestByMode: {},
          medalAchievedAt: {},
          curriculumVersion: CHALLENGE_CURRICULUM_VERSION
        };
      }

      const parsed =
        JSON.parse(raw);

      if (
        !parsed ||
        typeof parsed !== "object"
      ) {
        return {
          bestByMode: {},
          medalAchievedAt: {},
          curriculumVersion: CHALLENGE_CURRICULUM_VERSION
        };
      }

      if (
        !parsed.bestByMode ||
        typeof parsed.bestByMode !==
          "object"
      ) {
        parsed.bestByMode = {};
      }

      if (
        !parsed.medalAchievedAt ||
        typeof parsed.medalAchievedAt !==
          "object"
      ) {
        parsed.medalAchievedAt = {};
      }

      if (
        parsed.curriculumVersion !==
        CHALLENGE_CURRICULUM_VERSION
      ) {
        [
          "ba1", "ba2", "ba3", "ba4", "ba5",
          "bs1", "bs2", "bs3", "bs4", "bs5"
        ].forEach((mode) => {
          delete parsed.bestByMode[mode];
        });

        parsed.curriculumVersion =
          CHALLENGE_CURRICULUM_VERSION;

        try {
          localStorage.setItem(
            RANK_CERTIFICATION_STORAGE_KEY,
            JSON.stringify(parsed)
          );
        } catch (error) {}
      }

      return parsed;
    } catch (error) {
      return {
        bestByMode: {},
        medalAchievedAt: {}
      };
    }
  }

  let rankCertificationState =
    loadRankCertificationState();

  // v60以前の保存形式や、localStorage制限環境でも
  // メダル初期化がTOPナビゲーションを止めないよう必ず正規化する。
  if (
    !rankCertificationState ||
    typeof rankCertificationState !== "object"
  ) {
    rankCertificationState = {
      bestByMode: {},
      medalAchievedAt: {}
    };
  }

  if (
    !rankCertificationState.bestByMode ||
    typeof rankCertificationState.bestByMode !== "object"
  ) {
    rankCertificationState.bestByMode = {};
  }

  if (
    !rankCertificationState.medalAchievedAt ||
    typeof rankCertificationState.medalAchievedAt !== "object"
  ) {
    rankCertificationState.medalAchievedAt = {};
  }

  if (
    rankCertificationState.curriculumVersion !==
    CHALLENGE_CURRICULUM_VERSION
  ) {
    rankCertificationState.curriculumVersion =
      CHALLENGE_CURRICULUM_VERSION;
  }

  function saveRankCertificationState() {
    try {
      localStorage.setItem(
        RANK_CERTIFICATION_STORAGE_KEY,
        JSON.stringify(
          rankCertificationState
        )
      );
    } catch (error) {}
  }

  function updateRankCertificationState(
    record
  ) {
    if (
      !record ||
      record.remedialSkillId ||
      record.timeup ||
      !record.mode ||
      Number(record.totalQuestions) !==
        TOTAL_QUESTIONS
    ) {
      return;
    }

    if (
      Array.isArray(record.questions) &&
      record.questions.length > 0 &&
      !record.questions.every(
        (q) =>
          questionMatchesMode(
            record.mode,
            q
          )
      )
    ) {
      return;
    }

    const correct =
      Math.max(
        0,
        Math.min(
          TOTAL_QUESTIONS,
          Number(
            record.correctCount
          ) || 0
        )
      );

    const current =
      rankCertificationState
        .bestByMode[
          record.mode
        ];

    if (
      !current ||
      correct >
        Number(
          current.correct
        )
    ) {
      rankCertificationState
        .bestByMode[
          record.mode
        ] = {
          correct,
          achievedAt:
            record.finishedAt ||
            new Date()
              .toISOString()
        };

      saveRankCertificationState();
    }
  }

  function rebuildRankCertificationStateFromHistory() {
    (sessionHistory || [])
      .forEach(
        (record) => {
          updateRankCertificationState(
            record
          );
        }
      );
  }

  function bestCorrectForMode(
    mode
  ) {
    return Math.max(
      0,
      Math.min(
        TOTAL_QUESTIONS,
        Number(
          rankCertificationState
            .bestByMode[
              mode
            ]?.correct
        ) || 0
      )
    );
  }

  function certificationRuleSatisfied(
    rule
  ) {
    if (
      !rule ||
      !Array.isArray(
        rule.modes
      ) ||
      !rule.modes.length
    ) {
      return false;
    }

    const checks =
      rule.modes.map(
        (mode) =>
          bestCorrectForMode(
            mode
          ) >=
          rule.minCorrect
      );

    return (
      rule.type === "all"
        ? checks.every(Boolean)
        : checks.some(Boolean)
    );
  }

  function certificationRuleProgress(
    rule
  ) {
    if (
      !rule ||
      !Array.isArray(
        rule.modes
      ) ||
      !rule.modes.length
    ) {
      return 0;
    }

    const ratios =
      rule.modes.map(
        (mode) =>
          Math.max(
            0,
            Math.min(
              1,
              bestCorrectForMode(
                mode
              ) /
              Math.max(
                1,
                rule.minCorrect
              )
            )
          )
      );

    if (
      rule.type === "all"
    ) {
      return (
        ratios.reduce(
          (sum, value) =>
            sum + value,
          0
        ) /
        ratios.length *
        100
      );
    }

    return (
      Math.max(
        ...ratios
      ) * 100
    );
  }

  function shortModeName(
    mode
  ) {
    const title =
      modeInfo[
        mode
      ]?.title || mode;

    const pieces =
      String(title)
        .split("｜");

    return (
      pieces[
        pieces.length - 1
      ] || title
    );
  }

  function certificationEvidenceText(
    rule
  ) {
    if (!rule) {
      return "";
    }

    const parts =
      rule.modes.map(
        (mode) =>
          `${shortModeName(mode)} ${bestCorrectForMode(mode)}/12`
      );

    return parts.join("・");
  }

  function medalClassForRank(
    rankName
  ) {
    if (
      rankName === "10級" ||
      rankName === "9級" ||
      rankName === "8級"
    ) {
      return "rank-kyu-low";
    }

    if (
      rankName === "7級" ||
      rankName === "6級" ||
      rankName === "5級"
    ) {
      return "rank-kyu-mid";
    }

    if (
      rankName === "4級" ||
      rankName === "3級" ||
      rankName === "2級" ||
      rankName === "1級"
    ) {
      return "rank-kyu-high";
    }

    if (rankName === "初段") {
      return "rank-dan-1";
    }

    if (rankName === "二段") {
      return "rank-dan-2";
    }

    if (rankName === "三段") {
      return "rank-dan-3";
    }

    if (rankName === "名人") {
      return "rank-master";
    }

    return "";
  }

  function setMedalVisual(
    element,
    rankName
  ) {
    if (!element) {
      return;
    }

    element.classList.remove(
      "rank-kyu-low",
      "rank-kyu-mid",
      "rank-kyu-high",
      "rank-dan-1",
      "rank-dan-2",
      "rank-dan-3",
      "rank-master",
      "no-rank"
    );

    if (rankName === "見習い") {
      element.classList.add("no-rank");
    }

    const rankClass =
      medalClassForRank(
        rankName
      );

    if (rankClass) {
      element.classList.add(
        rankClass
      );
    }

    const textNode =
      element.querySelector(
        ".rank-medal-text"
      );

    if (textNode) {
      textNode.textContent =
        rankName === "見習い"
          ? "－"
          : rankName;
    }
  }

  function ensureEarnedMedalsThrough(
    rankIndex,
    achievedAt = null
  ) {
    if (
      !Number.isInteger(rankIndex) ||
      rankIndex < 0
    ) {
      return [];
    }

    const newlyEarned = [];
    const stamp =
      achievedAt ||
      new Date().toISOString();

    for (
      let i = 0;
      i <= rankIndex;
      i += 1
    ) {
      const rank =
        APP_RANK_CERTIFICATIONS[i];

      if (
        !rankCertificationState
          .medalAchievedAt[
            rank.name
          ]
      ) {
        rankCertificationState
          .medalAchievedAt[
            rank.name
          ] = stamp;

        newlyEarned.push(
          rank.name
        );
      }
    }

    if (newlyEarned.length) {
      saveRankCertificationState();
    }

    return newlyEarned;
  }

  function syncEarnedMedalsFromCurrentRank() {
    const rank =
      calculateAppRank();

    if (rank.currentIndex >= 0) {
      ensureEarnedMedalsThrough(
        rank.currentIndex
      );
    }
  }

  function isMedalEarned(
    rankName
  ) {
    return Boolean(
      rankCertificationState
        .medalAchievedAt[
          rankName
        ]
    );
  }

  function formatMedalDate(
    iso
  ) {
    if (!iso) {
      return "";
    }

    try {
      return new Date(iso)
        .toLocaleDateString(
          "ja-JP",
          {
            year: "numeric",
            month: "numeric",
            day: "numeric"
          }
        );
    } catch (error) {
      return "";
    }
  }

  function renderMedalCollection() {
    if (!medalCollectionGrid) {
      return;
    }

    medalCollectionGrid.innerHTML =
      "";

    APP_RANK_CERTIFICATIONS
      .forEach((rank) => {
        const earned =
          isMedalEarned(
            rank.name
          );

        const slot =
          document.createElement(
            "div"
          );

        slot.className =
          `medal-slot ${
            earned
              ? "earned"
              : "locked"
          }`;

        const medal =
          document.createElement(
            "div"
          );

        medal.className =
          "rank-medal";

        medal.innerHTML =
          `<span class="rank-medal-text">${rank.name}</span>`;

        setMedalVisual(
          medal,
          rank.name
        );

        const name =
          document.createElement(
            "div"
          );

        name.className =
          "medal-slot-name";

        name.textContent =
          rank.name;

        const status =
          document.createElement(
            "div"
          );

        status.className =
          "medal-slot-status";

        status.textContent =
          earned
            ? `獲得 ${formatMedalDate(
                rankCertificationState
                  .medalAchievedAt[
                    rank.name
                  ]
              )}`
            : "未獲得";

        slot.appendChild(medal);
        slot.appendChild(name);
        slot.appendChild(status);

        medalCollectionGrid.appendChild(
          slot
        );
      });
  }

  function buildRankConfetti() {
    if (!rankConfettiLayer) {
      return;
    }

    rankConfettiLayer.innerHTML =
      "";

    const colors = [
      "#f2c84b",
      "#ef7d67",
      "#6fc2d0",
      "#8d86d8",
      "#8cc36f",
      "#ffffff"
    ];

    for (
      let i = 0;
      i < 42;
      i += 1
    ) {
      const piece =
        document.createElement(
          "div"
        );

      piece.className =
        "rank-confetti";

      piece.style.left =
        `${(i * 37) % 100}%`;

      piece.style.background =
        colors[
          i %
          colors.length
        ];

      piece.style.setProperty(
        "--delay",
        `${(i % 9) * -0.23}s`
      );

      piece.style.setProperty(
        "--fall",
        `${2.2 + (i % 7) * 0.18}s`
      );

      piece.style.setProperty(
        "--drift",
        `${-55 + (i % 11) * 11}px`
      );

      rankConfettiLayer.appendChild(
        piece
      );
    }
  }

  function closeRankCelebration() {
    if (!rankCelebration) {
      return;
    }

    rankCelebration.classList.remove(
      "show"
    );

    rankCelebration.setAttribute(
      "aria-hidden",
      "true"
    );

    if (rankConfettiLayer) {
      rankConfettiLayer.innerHTML =
        "";
    }
  }

  function showRankCelebration(
    rankName,
    newlyEarned = []
  ) {
    if (
      !rankCelebration ||
      !rankName ||
      rankName === "見習い"
    ) {
      return;
    }

    setMedalVisual(
      rankCelebrationMedal,
      rankName
    );

    rankCelebrationMedalText.textContent =
      rankName;

    rankCelebrationTitle.textContent =
      `${rankName} 認定！`;

    rankCelebrationSub.textContent =
      newlyEarned.length > 1
        ? `一気に${newlyEarned.length}個のメダルを獲得しました。設定画面のメダル一覧に保存されています。`
        : "新しいメダルを獲得しました。設定画面のメダル一覧に保存されています。";

    buildRankConfetti();

    rankCelebration.classList.add(
      "show"
    );

    rankCelebration.setAttribute(
      "aria-hidden",
      "false"
    );

    if (
      "speechSynthesis" in window &&
      typeof SpeechSynthesisUtterance !==
        "undefined"
    ) {
      try {
        const utterance =
          new SpeechSynthesisUtterance(
            `${rankName}、認定！おめでとう！`
          );

        utterance.lang =
          "ja-JP";

        utterance.rate =
          0.95;

        utterance.pitch =
          1.08;

        const voice =
          getJapaneseVoice();

        if (voice) {
          utterance.voice =
            voice;
        }

        window.speechSynthesis.speak(
          utterance
        );
      } catch (error) {}
    }
  }

  function calculateAppRank() {
    let currentRule = null;
    let currentIndex = -1;

    APP_RANK_CERTIFICATIONS
      .forEach(
        (rule, index) => {
          if (
            certificationRuleSatisfied(
              rule
            )
          ) {
            currentRule =
              rule;

            currentIndex =
              index;
          }
        }
      );

    const nextRule =
      APP_RANK_CERTIFICATIONS[
        currentIndex + 1
      ] || null;

    const name =
      currentRule
        ? currentRule.name
        : "見習い";

    return {
      name,
      currentRule,
      currentIndex,
      currentEvidence:
        currentRule
          ? certificationEvidenceText(
              currentRule
            )
          : "",
      nextName:
        nextRule?.name ||
        null,
      nextRequirement:
        nextRule?.requirement ||
        null,
      nextProgress:
        nextRule
          ? certificationRuleProgress(
              nextRule
            )
          : 100
    };
  }

  function updateRankUi() {
    const rank =
      calculateAppRank();

    if (appRankBadge) {
      appRankBadge.textContent =
        rank.name;
    }

    if (appRankMeterFill) {
      appRankMeterFill.style.width =
        `${rank.nextProgress.toFixed(1)}%`;
    }

    if (appRankSub) {
      appRankSub.textContent =
        rank.currentRule
          ? `認定根拠：${rank.currentEvidence}`
          : "まだ級の認定条件を満たしていません。";
    }

    if (appRankNext) {
      appRankNext.textContent =
        rank.nextName
          ? `次の称号：${rank.nextName}｜${rank.nextRequirement}`
          : "現在、最高称号です。";
    }

    if (resultRankTitle) {
      resultRankTitle.textContent =
        rank.name;
    }

    if (resultRankProgress) {
      resultRankProgress.textContent =
        rank.nextName
          ? `次：${rank.nextName}｜${rank.nextRequirement}`
          : "最高称号";
    }

    if (homeRankName) {
      homeRankName.textContent =
        rank.name;
    }

    if (homeRankNext) {
      homeRankNext.textContent =
        rank.nextName
          ? `次は ${rank.nextName}`
          : rank.name === "見習い"
            ? "チャレンジで最初のメダルを獲得しよう"
            : "最高称号を獲得しています";
    }

    setMedalVisual(
      homeRankMedal,
      rank.name
    );

    renderMedalCollection();

    return rank;
  }

  function loadLearningStats() {
    try {
      const raw = localStorage.getItem(LEARNING_STORAGE_KEY);
      if (!raw) {
        return { problems: {} };
      }

      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        return { problems: {} };
      }

      if (!parsed.problems || typeof parsed.problems !== "object") {
        parsed.problems = {};
      }

      return parsed;
    } catch (error) {
      return { problems: {} };
    }
  }

  function saveLearningStats() {
    try {
      localStorage.setItem(
        LEARNING_STORAGE_KEY,
        JSON.stringify(learningStats)
      );
    } catch (error) {
      // 保存できない環境でもゲーム自体は継続する。
    }
  }

  function problemKey(mode, q) {
    return `${mode}|${q.op}|${q.a}|${q.b}`;
  }

  function getProblemStat(mode, q) {
    const key = problemKey(mode, q);

    if (!learningStats.problems[key]) {
      learningStats.problems[key] = {
        key,
        mode,
        a: q.a,
        b: q.b,
        op: q.op,
        attempts: 0,
        correct: 0,
        wrong: 0,
        recoveryStreak: 0,
        mastered: false,
        lastAt: 0
      };
    }

    return learningStats.problems[key];
  }

  function ensureQuestionTiming(
    questionIndex
  ) {
    if (!questionTimingLog[questionIndex]) {
      questionTimingLog[questionIndex] = {
        startPerf: null,
        responseMs: null,
        hintCount: 0
      };
    }

    return questionTimingLog[questionIndex];
  }

  function markQuestionDisplayed(
    questionIndex
  ) {
    const timing =
      ensureQuestionTiming(
        questionIndex
      );

    if (timing.startPerf === null) {
      timing.startPerf =
        performance.now();
    }
  }

  function markQuestionAnswered(
    questionIndex
  ) {
    const timing =
      ensureQuestionTiming(
        questionIndex
      );

    if (timing.startPerf !== null) {
      timing.responseMs =
        Math.max(
          0,
          performance.now() -
            timing.startPerf
        );
    }
  }

  function markQuestionHintUsed(
    questionIndex
  ) {
    const timing =
      ensureQuestionTiming(
        questionIndex
      );

    timing.hintCount += 1;
  }

  function recordAnswer(mode, q, ok) {
    const stat = getProblemStat(mode, q);

    stat.attempts += 1;
    stat.lastAt = Date.now();

    if (ok) {
      stat.correct += 1;

      if (q.isWeakness) {
        stat.recoveryStreak += 1;

        if (stat.recoveryStreak >= RECOVERY_STREAK_TO_MASTER) {
          stat.mastered = true;
        }
      }
    } else {
      stat.wrong += 1;
      stat.recoveryStreak = 0;
      stat.mastered = false;
    }

    saveLearningStats();
  }

  function getWeakCandidates(mode) {
    return Object.values(learningStats.problems)
      .filter((stat) =>
        stat.mode === mode &&
        stat.wrong > 0 &&
        !stat.mastered &&
        questionMatchesMode(mode, stat)
      )
      .sort((a, b) => {
        const scoreA =
          a.wrong * 4 -
          a.correct +
          Math.max(0, 2 - a.recoveryStreak) * 2;

        const scoreB =
          b.wrong * 4 -
          b.correct +
          Math.max(0, 2 - b.recoveryStreak) * 2;

        if (scoreB !== scoreA) {
          return scoreB - scoreA;
        }

        return b.lastAt - a.lastAt;
      });
  }

  function statToWeakQuestion(stat) {
    const q = makeQuestionFromFinal(
      stat.a,
      stat.op,
      stat.b
    );

    q.isWeakness = true;
    q.weaknessKey = stat.key;

    return q;
  }

  function makeQuestionFromFinal(a, op, b) {
    const q = {
      a,
      op,
      b,
      answer: calculate(a, op, b),
      isWeakness: false,
      weaknessKey: null
    };

    if (!isValidQuestion(q)) {
      throw new Error(
        `苦手問題の復元に失敗: ${a} ${op} ${b}`
      );
    }

    return q;
  }

  function getWeaknessQuestion(mode) {
    const active = getWeakCandidates(mode);

    // 未克服の苦手だけを出題する。
    // 苦手克服問題として1回正解して mastered=true になった問題は
    // getWeakCandidates() の対象外になるため、二度と苦手枠には出ない。
    if (active.length === 0) {
      return null;
    }

    const source = active[0];

    return statToWeakQuestion(source);
  }

  function makeFinalQuestion(
    a,
    op,
    b
  ) {
    const q = {
      a,
      op,
      b,
      answer: calculate(a, op, b),
      isWeakness: false,
      weaknessKey: null
    };

    if (!isValidQuestion(q)) {
      throw new Error(
        `補習問題生成失敗: ${a} ${op} ${b}`
      );
    }

    return q;
  }

  function makeRemedialQuestion(
    skillId
  ) {
    for (
      let tries = 0;
      tries < 200;
      tries += 1
    ) {
      let q = null;

      if (skillId === "add_direct") {
        const a = randInt(1, 3);
        const b = randInt(1, Math.max(1, 4 - a));
        q = makeFinalQuestion(a, "＋", b);
      } else if (skillId === "add_five") {
        const a = randInt(1, 4);
        const b = randInt(1, 4);

        if (a + b >= 5 && a + b <= 8) {
          q = makeFinalQuestion(a, "＋", b);
        }
      } else if (skillId === "add_ten") {
        const a = randInt(6, 9);
        const b = randInt(2, 9);

        if (a + b >= 10) {
          q = makeFinalQuestion(a, "＋", b);
        }
      } else if (skillId === "add_multi") {
        const a = randInt(20, 69);
        const b = randInt(10, 29);

        if (
          a + b <= 99 &&
          (a % 10) + (b % 10) >= 10
        ) {
          q = makeFinalQuestion(a, "＋", b);
        }
      } else if (skillId === "sub_direct") {
        const a = randInt(3, 9);
        const b = randInt(1, Math.min(4, a - 1));

        if (
          !(
            a >= 5 &&
            b < 5 &&
            (a % 5) < b
          )
        ) {
          q = makeFinalQuestion(a, "－", b);
        }
      } else if (skillId === "sub_five") {
        const a = randInt(5, 9);
        const b = randInt(1, 4);

        if ((a % 5) < b) {
          q = makeFinalQuestion(a, "－", b);
        }
      } else if (skillId === "sub_ten") {
        const ones = randInt(0, 8);
        const a = 10 + ones;
        const b = randInt(
          ones + 1,
          9
        );
        q = makeFinalQuestion(a, "－", b);
      } else if (skillId === "sub_multi") {
        const a = randInt(30, 99);
        const b = randInt(10, a - 1);

        if (
          (a % 10) < (b % 10)
        ) {
          q = makeFinalQuestion(a, "－", b);
        }
      } else if (skillId === "mul_basic") {
        q = makeFinalQuestion(
          randInt(1, 9),
          "×",
          randInt(1, 9)
        );
      } else if (skillId === "mul_place") {
        q = makeFinalQuestion(
          randInt(10, 99),
          "×",
          randInt(2, 9)
        );
      } else if (skillId === "div_basic") {
        const divisor = randInt(2, 9);
        const quotient = randInt(1, 9);
        q = makeFinalQuestion(
          divisor * quotient,
          "÷",
          divisor
        );
      } else if (skillId === "div_place") {
        const divisor = randInt(2, 9);
        const quotient = randInt(12, 99);
        q = makeFinalQuestion(
          divisor * quotient,
          "÷",
          divisor
        );
      }

      if (q) {
        q.remedialSkillId = skillId;
        return q;
      }
    }

    return makeOneQuestion(
      SKILL_DEFS[skillId]?.baseMode ||
      currentMode ||
      "ba1"
    );
  }

  function startRemedialPractice(
    skillId
  ) {
    const def =
      SKILL_DEFS[skillId];

    if (!def) {
      return;
    }

    // 補習は録画なし・時間制限なしで、考え方の定着を優先。
    resetSessionVideoData();

    startMode(
      def.baseMode,
      {
        remedialSkillId:
          skillId
      }
    );
  }

  function openSkillLesson(
    skillId
  ) {
    const def =
      SKILL_DEFS[skillId];

    if (!def) {
      return;
    }

    showLearningHome();

    openLearningLesson(
      def.category,
      def.lessonIndex
    );
  }

  function makeQuestionForIndex(questionIndex, mode) {
    if (activeRemedialSkillId) {
      return makeRemedialQuestion(
        activeRemedialSkillId
      );
    }

    // 1〜5問目は必ず通常問題。
    if (questionIndex < 5) {
      const q = makeOneQuestion(mode);
      q.isWeakness = false;
      return q;
    }

    // 6問目以降の指定スロットでは苦手問題を優先。
    if (WEAKNESS_SLOTS.has(questionIndex)) {
      const weak = getWeaknessQuestion(mode);

      if (weak) {
        sessionWeakAsked += 1;
        return weak;
      }
    }

    const q = makeOneQuestion(mode);
    q.isWeakness = false;
    return q;
  }

  function shouldLevelUp(mode, finalScore) {
    const activeWeakLeft = getWeakCandidates(mode).length;

    // 苦手克服問題が出た場合：
    // 出た苦手問題をすべてクリアし、未克服が残っておらず、
    // 全体でも10/12以上なら次のレベルへ。
    if (sessionWeakAsked > 0) {
      return (
        sessionWeakCorrect === sessionWeakAsked &&
        activeWeakLeft === 0 &&
        finalScore >= 10
      );
    }

    // 苦手が記録されていない場合は11/12以上でレベルアップ。
    return activeWeakLeft === 0 && finalScore >= 11;
  }

  function makeOneQuestion(mode) {
    if (mode === "ba1") return makeAdditionByOnesCarry(1, false);
    if (mode === "ba2") return makeAdditionByOnesCarry(1, true);
    if (mode === "ba3") return makeAdditionByOnesCarry(2, false);
    if (mode === "ba4") return makeAdditionByOnesCarry(2, true);
    if (mode === "ba5") return makeAdditionOnly(100, 999);

    if (mode === "bs1") return makeSubtractionByOnesBorrow(1, false);
    if (mode === "bs2") return makeSubtractionByOnesBorrow(1, true);
    if (mode === "bs3") return makeSubtractionByOnesBorrow(2, false);
    if (mode === "bs4") return makeSubtractionByOnesBorrow(2, true);
    if (mode === "bs5") return makeSubtractionOnly(100, 999);

    if (mode === "mm1") {
      return makeMul(1, 9, 1, 9);
    }

    if (mode === "mm2") {
      return makeMul(10, 99, 1, 9);
    }

    if (mode === "mm3") {
      return makeMul(10, 99, 10, 99);
    }

    if (mode === "md1") {
      return makeExactDivision(1, 81, 1, 9);
    }

    if (mode === "md2") {
      return makeExactDivision(100, 999, 1, 9);
    }

    if (mode === "md3") {
      return makeExactDivision(100, 999, 10, 99);
    }

    if (mode === "a1") return makeAddSub(1000, 9999);

    if (mode === "a2") {
      return coin()
        ? makeMul(100, 999, 10, 99)
        : makeExactDivision(1000, 9999, 10, 99);
    }

    return coin()
      ? makeMul(100, 999, 100, 999)
      : makeExactDivision(10000, 99999, 100, 999);
  }

  function generateQuestions(mode) {
    // 問題はセッション中に1問ずつ生成する。
    // これにより最初の5問で見つかった苦手も、
    // 6問目以降の苦手克服問題へ反映できる。
    return [];
  }

  function placeName(place) {
    const names = {
      1: "一の位",
      10: "十の位",
      100: "百の位",
      1000: "千の位",
      10000: "万の位",
      100000: "十万の位",
      1000000: "百万の位"
    };

    return names[place] || `${place}の位`;
  }

  function shortPlaceName(place) {
    const names = {
      1: "一",
      10: "十",
      100: "百",
      1000: "千",
      10000: "万",
      100000: "十万",
      1000000: "百万"
    };

    return names[place] || "";
  }

  function buildSorobanBoard(digitCount) {
    hintDigitCount = Math.max(1, digitCount);
    hintRods = [];
    sorobanBoard.innerHTML = "";

    for (let i = 0; i < hintDigitCount; i += 1) {
      const place = 10 ** (hintDigitCount - 1 - i);

      const rod = document.createElement("div");
      rod.className = "soroban-rod";
      rod.dataset.place = String(place);

      const line = document.createElement("div");
      line.className = "rod-line";
      rod.appendChild(line);

      const upper = document.createElement("div");
      upper.className = "soroban-bead upper-bead";
      rod.appendChild(upper);

      const lowers = [];

      for (let j = 0; j < 4; j += 1) {
        const bead = document.createElement("div");
        bead.className = "soroban-bead lower-bead";
        bead.style.top = `${112 + j * 18}px`;
        rod.appendChild(bead);
        lowers.push(bead);
      }

      const label = document.createElement("div");
      label.className = "rod-place";
      label.textContent = shortPlaceName(place);
      rod.appendChild(label);

      sorobanBoard.appendChild(rod);

      hintRods.push({
        rod,
        place,
        upper,
        lowers
      });
    }
  }

  function setRodDigit(rodInfo, digit) {
    const safeDigit = Math.max(0, Math.min(9, Number(digit) || 0));
    const upperOn = safeDigit >= 5;
    const lowerCount = safeDigit % 5;

    rodInfo.upper.style.top =
      upperOn ? "50px" : "13px";

    rodInfo.lowers.forEach((bead, index) => {
      const top = index < lowerCount
        ? 89 + index * 18
        : 121 + index * 18;

      bead.style.top = `${top}px`;
    });
  }

  function showSorobanNumber(value, activePlace = null) {
    const safeValue = Math.max(
      0,
      Math.floor(Math.abs(Number(value) || 0))
    );

    const digits = String(safeValue)
      .padStart(hintDigitCount, "0")
      .slice(-hintDigitCount)
      .split("")
      .map(Number);

    hintRods.forEach((rodInfo, index) => {
      setRodDigit(rodInfo, digits[index]);

      rodInfo.rod.classList.toggle(
        "active",
        activePlace !== null &&
        rodInfo.place === activePlace
      );
    });
  }

  function decomposeByPlace(value) {
    const out = [];
    const digits = String(Math.abs(Math.floor(value))).split("");

    digits.forEach((char, index) => {
      const digit = Number(char);
      if (digit === 0) return;

      const place =
        10 ** (digits.length - 1 - index);

      out.push({
        digit,
        place,
        amount: digit * place
      });
    });

    return out;
  }

  function hintDigitAt(
    value,
    place
  ) {
    return Math.floor(
      Math.abs(value) /
      place
    ) % 10;
  }

  function pushHintMove(
    steps,
    state,
    delta,
    activePlace,
    text
  ) {
    state.value += delta;

    steps.push({
      value: state.value,
      activePlace,
      text
    });
  }

  function pushHintExplain(
    steps,
    state,
    activePlace,
    text
  ) {
    steps.push({
      value: state.value,
      activePlace,
      text
    });
  }

  function addWithinDigitHint(
    steps,
    state,
    place,
    amount,
    context = ""
  ) {
    if (amount <= 0) {
      return;
    }

    const digit =
      hintDigitAt(
        state.value,
        place
      );

    const prefix =
      context
        ? `${context} `
        : "";

    // 5以上を足す場合、五珠を使えるなら先に五珠。
    if (
      amount >= 5 &&
      digit < 5
    ) {
      pushHintMove(
        steps,
        state,
        5 * place,
        place,
        `${prefix}${placeName(place)}で、まず5の珠を入れます。`
      );

      const rest =
        amount - 5;

      if (rest > 0) {
        pushHintMove(
          steps,
          state,
          rest * place,
          place,
          `${placeName(place)}で、残りの${rest}を足します。`
        );
      }

      return;
    }

    // 5をまたぐ足し算。
    // 例: 2+4 → 先に-1、そのあと+5。
    if (
      digit < 5 &&
      amount < 5 &&
      digit + amount >= 5
    ) {
      const complement =
        5 - amount;

      pushHintExplain(
        steps,
        state,
        place,
        `${placeName(place)}で5をまたぎます。${amount}を足すには、5の補数は${complement}です。先に${complement}を引きます。`
      );

      pushHintMove(
        steps,
        state,
        -complement * place,
        place,
        `先に${complement}を引きます。`
      );

      pushHintMove(
        steps,
        state,
        5 * place,
        place,
        `次に5の珠を入れます。${complement}を引いて5を足すと、合わせて${amount}を足したことになります。`
      );

      return;
    }

    pushHintMove(
      steps,
      state,
      amount * place,
      place,
      `${prefix}${placeName(place)}で${amount}をそのまま足します。`
    );
  }

  function subtractWithinDigitHint(
    steps,
    state,
    place,
    amount,
    context = ""
  ) {
    if (amount <= 0) {
      return;
    }

    const digit =
      hintDigitAt(
        state.value,
        place
      );

    const prefix =
      context
        ? `${context} `
        : "";

    if (
      digit >= 5 &&
      amount >= 5
    ) {
      pushHintMove(
        steps,
        state,
        -5 * place,
        place,
        `${prefix}${placeName(place)}で、まず5の珠を外します。`
      );

      const rest =
        amount - 5;

      if (rest > 0) {
        pushHintMove(
          steps,
          state,
          -rest * place,
          place,
          `${placeName(place)}で、残りの${rest}を引きます。`
        );
      }

      return;
    }

    // 5をまたぐ引き算。
    // 例: 7-4 → -5、そのあと+1。
    if (
      digit >= 5 &&
      amount < 5 &&
      digit - 5 < amount
    ) {
      const complement =
        5 - amount;

      pushHintExplain(
        steps,
        state,
        place,
        `${placeName(place)}で5をまたぎます。${amount}を引くには、5の補数は${complement}です。先に5の珠を外します。`
      );

      pushHintMove(
        steps,
        state,
        -5 * place,
        place,
        `先に5を引きます。`
      );

      pushHintMove(
        steps,
        state,
        complement * place,
        place,
        `次に${complement}を足します。5を引いて${complement}を戻すと、合わせて${amount}を引いたことになります。`
      );

      return;
    }

    pushHintMove(
      steps,
      state,
      -amount * place,
      place,
      `${prefix}${placeName(place)}で${amount}をそのまま引きます。`
    );
  }

  function addDigitWithCarryHint(
    steps,
    state,
    place,
    amount,
    carryContext = false
  ) {
    if (amount <= 0) {
      return;
    }

    const digit =
      hintDigitAt(
        state.value,
        place
      );

    if (
      digit + amount <= 9
    ) {
      addWithinDigitHint(
        steps,
        state,
        place,
        amount,
        carryContext
          ? "くり上がりです。"
          : ""
      );
      return;
    }

    const complement =
      10 - amount;

    pushHintExplain(
      steps,
      state,
      place,
      `${placeName(place)}で10をまたぎます。${amount}を足すときの10の補数は${complement}です。先に${complement}を引いてから、1つ上の位へ1を足します。`
    );

    // ユーザー指定どおり、先にマイナス側の珠を動かす。
    subtractWithinDigitHint(
      steps,
      state,
      place,
      complement,
      "10をまたぐので、"
    );

    addDigitWithCarryHint(
      steps,
      state,
      place * 10,
      1,
      true
    );
  }

  function subtractDigitWithBorrowHint(
    steps,
    state,
    place,
    amount,
    borrowContext = false
  ) {
    if (amount <= 0) {
      return;
    }

    const digit =
      hintDigitAt(
        state.value,
        place
      );

    if (
      digit >= amount
    ) {
      subtractWithinDigitHint(
        steps,
        state,
        place,
        amount,
        borrowContext
          ? "くり下がりです。"
          : ""
      );
      return;
    }

    const complement =
      10 - amount;

    pushHintExplain(
      steps,
      state,
      place,
      `${placeName(place)}だけでは${amount}を引けません。10の補数は${complement}です。先に1つ上の位から1を引き、そのあと${placeName(place)}へ${complement}を足します。`
    );

    // 先に上の位を1つ引く。これが繰り下がり。
    subtractDigitWithBorrowHint(
      steps,
      state,
      place * 10,
      1,
      true
    );

    addWithinDigitHint(
      steps,
      state,
      place,
      complement,
      "10から借りた分を使って、"
    );
  }

  function buildAddSubHintSteps(q) {
    const steps = [
      {
        value: q.a,
        activePlace: null,
        text:
          `まず、${q.a}をそろばんに置きます。黄色く光る位を見ながら、ゆっくり動かします。`
      }
    ];

    const state = {
      value: q.a
    };

    const parts =
      decomposeByPlace(
        q.b
      );

    parts.forEach((part) => {
      pushHintExplain(
        steps,
        state,
        part.place,
        `${placeName(part.place)}の${part.digit}を${q.op === "＋" ? "足します" : "引きます"}。`
      );

      if (q.op === "＋") {
        addDigitWithCarryHint(
          steps,
          state,
          part.place,
          part.digit
        );
      } else {
        subtractDigitWithBorrowHint(
          steps,
          state,
          part.place,
          part.digit
        );
      }
    });

    steps.push({
      value: q.answer,
      activePlace: null,
      text:
        `できました。答えは${q.answer}です。もう一度見るときは、先に動く珠と、あとから動く珠を見てみよう。`
    });

    return steps;
  }

  function buildResultPlacementSteps(q) {
    const answer = Math.max(0, Math.floor(q.answer));
    const parts = decomposeByPlace(answer);

    const steps = [
      {
        value: 0,
        activePlace: null,
        text:
          q.op === "×"
            ? "掛け算では、答えの珠の置き方を位ごとに見てみます。"
            : "割り算では、答えの珠の置き方を位ごとに見てみます。"
      }
    ];

    let current = 0;

    parts.forEach((part) => {
      current += part.amount;

      steps.push({
        value: current,
        activePlace: part.place,
        text:
          `${placeName(part.place)}に ${part.digit} を置きます。`
      });
    });

    steps.push({
      value: answer,
      activePlace: null,
      text: "この珠の形で完成です。"
    });

    return steps;
  }

  function buildHintSteps(q) {
    if (q.op === "＋" || q.op === "－") {
      return buildAddSubHintSteps(q);
    }

    return buildResultPlacementSteps(q);
  }

  function stopSorobanHintAnimation() {
    hintRunToken += 1;
    hintNarrationToken += 1;

    if (
      hintNarrationActive &&
      "speechSynthesis" in window
    ) {
      window.speechSynthesis.cancel();
    }
  }

  function hideSorobanHint() {
    stopSorobanHintAnimation();

    hintNarrationActive = false;
    hintManualSteps = [];
    hintManualIndex = 0;
    refreshBgmVolume();

    abacusHint.classList.remove("show");
    hintStep.textContent = "";
    hintStepCounter.textContent = "";
    sorobanBoard.innerHTML = "";
    hintRods = [];

    // ヒント前から音声回答ONだった場合は、
    // 「ゴワサン」を言い直さず、同じ問題でマイクだけ戻す。
    if (
      voiceAnswerEnabled &&
      !locked &&
      workspace.classList.contains("show") &&
      !results.classList.contains("show")
    ) {
      scheduleSilentVoiceRestart(
        420
      );
    }
  }

  function speakHintNarration(
    message
  ) {
    if (
      !hintNarrationActive ||
      !message
    ) {
      return;
    }

    if (
      !("speechSynthesis" in window) ||
      typeof SpeechSynthesisUtterance ===
        "undefined"
    ) {
      return;
    }

    window.speechSynthesis.cancel();

    const narrationToken =
      ++hintNarrationToken;

    const utterance =
      new SpeechSynthesisUtterance(
        message
      );

    utterance.lang = "ja-JP";
    utterance.rate = 0.72;
    utterance.pitch = 1.04;
    utterance.volume = 1.0;

    const voice =
      getJapaneseVoice();

    if (voice) {
      utterance.voice = voice;
    }

    utterance.onend = () => {
      if (
        narrationToken !==
        hintNarrationToken
      ) {
        return;
      }
    };

    utterance.onerror =
      utterance.onend;

    window.speechSynthesis.speak(
      utterance
    );
  }

  function renderManualHintStep(
    stepIndex,
    speak = true
  ) {
    if (
      !hintManualSteps.length ||
      !abacusHint.classList.contains(
        "show"
      )
    ) {
      return;
    }

    const safeIndex =
      Math.max(
        0,
        Math.min(
          hintManualSteps.length - 1,
          Number(stepIndex) || 0
        )
      );

    hintManualIndex =
      safeIndex;

    const step =
      hintManualSteps[
        hintManualIndex
      ];

    // 「進む」「戻る」を押した瞬間だけ珠の状態を変える。
    showSorobanNumber(
      step.value,
      step.activePlace
    );

    hintStep.textContent =
      step.text;

    hintStepCounter.textContent =
      `STEP ${hintManualIndex + 1} / ${hintManualSteps.length}`;

    hintPrevBtn.disabled =
      hintManualIndex <= 0;

    hintNextBtn.disabled =
      hintManualIndex >=
      hintManualSteps.length - 1;

    hintNextBtn.textContent =
      hintManualIndex >=
        hintManualSteps.length - 1
        ? "ここで完成"
        : "進む →";

    if (speak) {
      speakHintNarration(
        step.text
      );
    }
  }

  function moveManualHint(
    direction
  ) {
    if (
      !hintManualSteps.length
    ) {
      return;
    }

    const nextIndex =
      hintManualIndex +
      direction;

    if (
      nextIndex < 0 ||
      nextIndex >=
        hintManualSteps.length
    ) {
      return;
    }

    renderManualHintStep(
      nextIndex,
      true
    );
  }

  function runSorobanHint() {
    if (sessionVideoActive) {
      feedback.textContent =
        "撮影中は珠ヒントを使わず、終了後の解説・補習で復習します。";
      feedback.className =
        "feedback";
      return;
    }

    if (
      index >= TOTAL_QUESTIONS ||
      !questions[index]
    ) {
      return;
    }

    const q =
      questions[index];

    markQuestionHintUsed(
      index
    );

    recordVideoHintUse(
      index
    );

    const steps =
      buildHintSteps(q);

    const maxValue =
      Math.max(
        q.a,
        q.b,
        Math.abs(q.answer),
        ...steps.map(
          (step) =>
            Math.abs(step.value)
        )
      );

    const digitCount =
      Math.max(
        1,
        String(
          Math.floor(maxValue)
        ).length
      );

    stopSorobanHintAnimation();

    // ヒントの音声と回答用マイクを同時に動かさない。
    hintNarrationActive = true;
    clearVoiceRestartTimer();
    stopVoiceRecognition();
    refreshBgmVolume();

    buildSorobanBoard(
      digitCount
    );

    abacusHint.classList.add(
      "show"
    );

    hintManualSteps =
      steps;

    hintManualIndex = 0;

    // 開いた直後はSTEP1で停止。
    // 以後は「進む」「戻る」を押した時だけ珠が変わる。
    renderManualHintStep(
      0,
      true
    );
  }

  function getTargetBgmVolume() {
    // 問題を考えている間は、マイクがONでもBGMを通常音量で流す。
    // 実際の発話をSpeechRecognitionが検知した瞬間だけ0%へ下げる。
    if (
      voiceSpeechDetected ||
      anzanSpeechDetected
    ) {
      return BGM_VOLUME_LISTENING;
    }

    if (
      voiceCalloutRunning ||
      voicePauseForFeedback ||
      hintNarrationActive
    ) {
      return BGM_VOLUME_APP_SPEECH;
    }

    return BGM_VOLUME_NORMAL;
  }

  function refreshBgmVolume() {
    bgmEngine.setVolume(
      getTargetBgmVolume()
    );
  }

  function startBgmKeepAlive() {
    if (bgmKeepAliveTimer) {
      return;
    }

    bgmKeepAliveTimer =
      setInterval(() => {
        if (
          !bgmOn ||
          !voiceAnswerEnabled ||
          !workspace.classList.contains("show") ||
          results.classList.contains("show")
        ) {
          return;
        }

        // 音声認識中にブラウザがAudioContextを
        // suspendしていないか定期的に確認する。
        bgmEngine.keepAlive();

        if (
          currentBgm &&
          !bgmEngine.hasActiveSource()
        ) {
          bgmEngine.play(
            currentBgm,
            true
          );
        }

        refreshBgmVolume();
      }, 400);
  }

  function stopBgmKeepAlive() {
    if (!bgmKeepAliveTimer) {
      return;
    }

    clearInterval(
      bgmKeepAliveTimer
    );

    bgmKeepAliveTimer = null;
  }

  function chooseRandomTrack(tracks) {
    return tracks[
      Math.floor(
        Math.random() * tracks.length
      )
    ];
  }

  function desiredBgmName(streak) {
    let groupKey;
    let tracks;

    if (streak <= 2) {
      groupKey = "1-3";
      tracks = [
        "1.mp3",
        "2.mp3",
        "3.mp3"
      ];
    } else if (streak <= 5) {
      groupKey = "4-6";
      tracks = [
        "4.mp3",
        "5.mp3",
        "6.mp3"
      ];
    } else if (streak <= 9) {
      groupKey = "7-9";
      tracks = [
        "7.mp3",
        "8.mp3",
        "9.mp3"
      ];
    } else {
      groupKey = "10";
      tracks = [
        "10-1.mp3",
        "10-2.mp3",
        "10-3.mp3"
      ];
    }

    // 同じ連続正解帯の中では、選んだ曲をそのまま流す。
    if (
      currentBgmGroupKey === groupKey &&
      currentBgm &&
      tracks.includes(currentBgm)
    ) {
      return currentBgm;
    }

    // 次の帯に入った時だけ、その帯からランダム選択。
    currentBgmGroupKey = groupKey;

    return chooseRandomTrack(tracks);
  }

  function updateBgmButton() {
    bgmBtn.textContent =
      bgmOn
        ? "♪ BGM ON"
        : "♪ BGM OFF";
  }

  function playBgmForStreak(
    streak,
    force = false
  ) {
    const name =
      desiredBgmName(streak);

    currentBgm = name;

    bgmName.textContent =
      `BGM：${name}`;

    if (!bgmOn) {
      return;
    }

    // ここではマイク状態にかかわらず
    // BGM再生ノードを継続する。
    bgmEngine.setVolume(
      getTargetBgmVolume(),
      0.08
    );

    bgmEngine
      .play(name, force)
      .then((ok) => {
        if (!ok) {
          bgmName.textContent =
            `BGM：${name}（再生できません）`;
          return;
        }

        refreshBgmVolume();
      })
      .catch(() => {
        bgmName.textContent =
          `BGM：${name}（再生できません）`;
      });
  }

  function stopBgm() {
    stopBgmKeepAlive();
    bgmEngine.stop();
  }

  function toggleBgm() {
    bgmOn = !bgmOn;
    saveBooleanSetting(
      BGM_SETTING_KEY,
      bgmOn
    );
    updateBgmButton();
    updateSettingsUi();

    if (
      bgmOn &&
      workspace.classList.contains("show") &&
      !results.classList.contains("show")
    ) {
      // BGMボタンはユーザー操作なので、
      // このイベント内でAudioContextを起動する。
      bgmEngine.ensureContext();

      playBgmForStreak(
        correctStreak,
        true
      );

      if (voiceAnswerEnabled) {
        startBgmKeepAlive();
      }
    } else {
      stopBgm();
    }
  }


  function getSpeechRecognitionClass() {
    return (
      window.SpeechRecognition ||
      window.webkitSpeechRecognition ||
      null
    );
  }

  function japaneseDigitValue(char) {
    const values = {
      "〇": 0,
      "零": 0,
      "一": 1,
      "二": 2,
      "三": 3,
      "四": 4,
      "五": 5,
      "六": 6,
      "七": 7,
      "八": 8,
      "九": 9
    };

    if (
      Object.prototype.hasOwnProperty
        .call(values, char)
    ) {
      return values[char];
    }

    return null;
  }

  function japaneseIntegerToNumber(input) {
    const original =
      String(input || "");

    const compact =
      original
        .replace(
          /[,\s、。,.]/g,
          ""
        )
        .replace(
          /マイナス|minus/gi,
          "-"
        );

    if (!compact) {
      return null;
    }

    // 「15」のように数字で認識された場合。
    const direct =
      compact.match(/-?\d+/);

    if (direct) {
      const value =
        Number(direct[0]);

      return Number.isFinite(value)
        ? value
        : null;
    }

    // ひらがな認識も漢数字へ寄せる。
    const normalized =
      compact
        .replace(/いち/g, "一")
        .replace(/に/g, "二")
        .replace(/さん/g, "三")
        .replace(/よん|し/g, "四")
        .replace(/ご/g, "五")
        .replace(/ろく/g, "六")
        .replace(/なな|しち/g, "七")
        .replace(/はち/g, "八")
        .replace(/きゅう|く/g, "九")
        .replace(/れい|ぜろ/g, "零")
        .replace(/じゅう/g, "十")
        .replace(/ひゃく/g, "百")
        .replace(/せん/g, "千")
        .replace(/まん/g, "万");

    if (
      !/[一二三四五六七八九〇零十百千万]/
        .test(normalized)
    ) {
      return null;
    }

    let total = 0;
    let section = 0;
    let number = 0;

    const negative =
      normalized.startsWith("-");

    const chars =
      normalized
        .replace("-", "");

    for (const char of chars) {
      const digit =
        japaneseDigitValue(char);

      if (digit !== null) {
        number = digit;
        continue;
      }

      if (
        char === "十" ||
        char === "百" ||
        char === "千"
      ) {
        const unit =
          char === "十"
            ? 10
            : char === "百"
              ? 100
              : 1000;

        section +=
          (number || 1) * unit;

        number = 0;
        continue;
      }

      if (char === "万") {
        section += number;

        total +=
          (section || 1) *
          10000;

        section = 0;
        number = 0;
      }
    }

    const value =
      total +
      section +
      number;

    return negative
      ? -value
      : value;
  }

  function normalizeNumericSpeechText(
    input
  ) {
    return String(
      input || ""
    )
      .toLowerCase()
      .replace(
        /[,\s、。,.！!？?]/g,
        ""
      )
      .replace(
        /答えは|答え|こたえは|こたえ|です|だよ|だね|だ|かな|えっと|えーと|ええと|えー|うーん|はい/g,
        ""
      )
      .replace(
        /マイナス|minus/g,
        "-"
      )
      .replace(/いち/g, "一")
      .replace(/に/g, "二")
      .replace(/さん/g, "三")
      .replace(/よん|し/g, "四")
      .replace(/ご/g, "五")
      .replace(/ろく/g, "六")
      .replace(/なな|しち/g, "七")
      .replace(/はち/g, "八")
      .replace(/きゅう|く/g, "九")
      .replace(/れい|ぜろ/g, "零")
      .replace(/じゅう/g, "十")
      .replace(/ひゃく/g, "百")
      .replace(/せん/g, "千")
      .replace(/まん/g, "万");
  }

  function isLikelyNumericSpeech(
    input
  ) {
    const normalized =
      normalizeNumericSpeechText(
        input
      );

    if (!normalized) {
      return false;
    }

    // 数字・漢数字以外の言葉が残っている候補は、
    // BGMや周囲の会話の可能性が高いので採用しない。
    return (
      /^-?\d+$/.test(
        normalized
      ) ||
      /^-?[一二三四五六七八九〇零十百千万]+$/.test(
        normalized
      )
    );
  }

  function selectBestNumericSpeech(
    result
  ) {
    if (!result) {
      return null;
    }

    let best = null;

    for (
      let i = 0;
      i < result.length;
      i += 1
    ) {
      const alternative =
        result[i];

      const transcript =
        String(
          alternative?.transcript || ""
        ).trim();

      if (
        !transcript ||
        !isLikelyNumericSpeech(
          transcript
        )
      ) {
        continue;
      }

      const value =
        japaneseIntegerToNumber(
          transcript
        );

      if (
        value === null ||
        !Number.isInteger(value) ||
        value < 0
      ) {
        continue;
      }

      const rawConfidence =
        Number(
          alternative?.confidence
        );

      const confidence =
        Number.isFinite(
          rawConfidence
        )
          ? rawConfidence
          : 0;

      // ブラウザがconfidenceを返している場合、
      // 極端に低い数字候補はBGMや環境音の誤認識として捨てる。
      if (
        confidence > 0 &&
        confidence < 0.22
      ) {
        continue;
      }

      const normalized =
        normalizeNumericSpeechText(
          transcript
        );

      const digitOnlyBonus =
        /^-?\d+$/.test(
          normalized
        )
          ? 0.15
          : 0;

      const score =
        confidence +
        digitOnlyBonus -
        Math.min(
          0.12,
          transcript.length *
            0.002
        );

      if (
        !best ||
        score > best.score
      ) {
        best = {
          value,
          transcript,
          confidence,
          score
        };
      }
    }

    return best;
  }

  function updateVoiceUi(
    message = null
  ) {
    const supported =
      Boolean(
        getSpeechRecognitionClass()
      );

    if (!supported) {
      voiceBtn.disabled = true;
      voiceBtn.textContent =
        "🎤 音声回答 非対応";

      voiceStatus.textContent =
        "このブラウザでは音声回答を利用できません。";

      voiceStatus.classList.remove(
        "listening"
      );

      return;
    }

    voiceBtn.disabled = false;

    voiceBtn.classList.toggle(
      "on",
      voiceAnswerEnabled
    );

    voiceBtn.textContent =
      voiceAnswerEnabled
        ? "🎤 音声回答 ON"
        : "🎤 音声回答 OFF";

    voiceStatus.textContent =
      message ||
      (
        voiceAnswerEnabled
          ? "音声回答：数字を話してください"
          : "音声回答：OFF"
      );

    voiceStatus.classList.toggle(
      "listening",
      voiceAnswerEnabled &&
      voiceRecognitionRunning &&
      !voicePauseForFeedback
    );
  }

  function clearVoiceRestartTimer() {
    if (!voiceRestartTimer) {
      return;
    }

    clearTimeout(
      voiceRestartTimer
    );

    voiceRestartTimer = null;
  }

  function stopVoiceRecognition() {
    clearVoiceRestartTimer();

    if (!voiceRecognition) {
      voiceRecognitionRunning = false;
      refreshBgmVolume();
      return;
    }

    if (voiceRecognitionRunning) {
      try {
        voiceRecognition.stop();
      } catch (error) {}
    }
  }

  function scheduleVoiceRestart(
    delay = 280
  ) {
    if (
      !voiceAnswerEnabled ||
      voicePauseForFeedback ||
      hintNarrationActive ||
      locked ||
      !workspace.classList.contains("show") ||
      results.classList.contains("show")
    ) {
      return;
    }

    clearVoiceRestartTimer();

    voiceRestartTimer =
      setTimeout(() => {
        if (
          gowasanSpokenForIndex ===
          index
        ) {
          startMicrophoneRecognition();
          return;
        }

        startVoiceRecognition();
      }, delay);
  }

  function scheduleSilentVoiceRestart(
    delay = 320
  ) {
    if (
      !voiceAnswerEnabled ||
      voicePauseForFeedback ||
      voiceCalloutRunning ||
      hintNarrationActive ||
      locked ||
      !workspace.classList.contains("show") ||
      results.classList.contains("show")
    ) {
      return;
    }

    clearVoiceRestartTimer();

    voiceRestartTimer =
      setTimeout(() => {
        startMicrophoneRecognition();
      }, delay);
  }

  function handleRecognizedSpeech(
    event
  ) {
    if (
      locked ||
      !voiceAnswerEnabled ||
      voicePauseForFeedback ||
      voiceCalloutRunning
    ) {
      return;
    }

    const result =
      event.results?.[0];

    if (!result) {
      return;
    }

    // 問題はマイクonstart後に初めて表示されるため、
    // 450ms未満で返る認識結果は子どもの回答とは考えにくい。
    // 直前のBGM・ゴワサンの残響として捨てる。
    if (
      voiceListeningStartedAt > 0 &&
      Date.now() -
        voiceListeningStartedAt <
        MIC_RESULT_GUARD_MS
    ) {
      updateVoiceUi(
        "音声回答：数字を話してください"
      );
      return;
    }

    const selected =
      selectBestNumericSpeech(
        result
      );

    if (!selected) {
      const firstText =
        String(
          result[0]?.transcript || ""
        );

      updateVoiceUi(
        firstText
          ? "音声回答：数字だけを、もう一度話してください"
          : "音声回答：もう一度話してください"
      );
      return;
    }

    answerInput.value =
      String(
        selected.value
      );

    // この認識結果は回答として採用する。
    // stop()後のonendが、回答確定前にマイクを再起動しないようにする。
    voicePauseForFeedback = true;

    updateVoiceUi(
      `音声回答：「${selected.transcript}」→ ${selected.value}`
    );

    stopVoiceRecognition();

    setTimeout(() => {
      if (!locked) {
        submitAnswer();
      }
    }, 160);
  }

  function createVoiceRecognition() {
    const Recognition =
      getSpeechRecognitionClass();

    if (!Recognition) {
      return null;
    }

    const recognition =
      new Recognition();

    recognition.lang = "ja-JP";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 5;

    recognition.onstart = () => {
      voiceRecognitionRunning = true;
      voiceListeningStartedAt =
        Date.now();
      voiceSpeechDetected = false;

      // マイクがONでも、考えている間のBGMは通常音量。
      refreshBgmVolume();
      bgmEngine.ensureRunning();
      startBgmKeepAlive();

      updateVoiceUi(
        "音声回答：数字を話してください"
      );

      // ① ゴワサン終了
      // ② マイクの onstart を確認
      // ③ ここで初めて問題を生成・表示する
      renderQuestion(true);
    };

    recognition.onspeechstart = () => {
      // ブラウザが人の発話を検知した瞬間だけBGMを消す。
      voiceSpeechDetected = true;
      refreshBgmVolume();

      updateVoiceUi(
        "音声回答：聞いています…"
      );
    };

    recognition.onspeechend = () => {
      // 結果確定まではBGMを戻さず、語尾をBGMで汚さない。
      updateVoiceUi(
        "音声回答：確認中…"
      );
    };

    recognition.onresult =
      handleRecognizedSpeech;

    recognition.onend = () => {
      voiceRecognitionRunning = false;
      voiceListeningStartedAt = 0;
      voiceSpeechDetected = false;

      refreshBgmVolume();
      bgmEngine.ensureRunning();

      if (
        voiceAnswerEnabled &&
        !voicePauseForFeedback &&
        !voiceCalloutRunning &&
        !locked &&
        workspace.classList.contains("show") &&
        !results.classList.contains("show")
      ) {
        // 無音タイムアウトでは「ゴワサン！」を言い直さない。
        // 同じ問題を表示したまま、マイクだけ静かに再開する。
        scheduleSilentVoiceRestart(
          320
        );
      } else {
        updateVoiceUi();
      }
    };

    recognition.onerror = (
      event
    ) => {
      voiceRecognitionRunning = false;
      voiceListeningStartedAt = 0;
      voiceSpeechDetected = false;

      refreshBgmVolume();
      bgmEngine.ensureRunning();

      if (
        event.error ===
        "not-allowed"
      ) {
        voiceAnswerEnabled = false;
        voiceCalloutRunning = false;
        stopBgmKeepAlive();

        updateVoiceUi(
          "マイクが許可されていません。ブラウザでマイクを許可してください。"
        );
        return;
      }

      if (
        event.error === "no-speech" ||
        event.error === "aborted"
      ) {
        if (
          voiceAnswerEnabled &&
          !voicePauseForFeedback &&
          !voiceCalloutRunning &&
          !locked
        ) {
          scheduleSilentVoiceRestart(
            380
          );
        }
        return;
      }

      updateVoiceUi(
        `音声回答：${event.error}`
      );

      if (
        voiceAnswerEnabled &&
        !voicePauseForFeedback &&
        !voiceCalloutRunning &&
        !locked
      ) {
        scheduleSilentVoiceRestart(500);
      }
    };

    return recognition;
  }

  function startMicrophoneRecognition() {
    if (
      !voiceAnswerEnabled ||
      voicePauseForFeedback ||
      voiceCalloutRunning ||
      locked ||
      voiceRecognitionRunning ||
      results.classList.contains("show") ||
      !workspace.classList.contains("show")
    ) {
      return;
    }

    if (!voiceRecognition) {
      voiceRecognition =
        createVoiceRecognition();
    }

    if (!voiceRecognition) {
      updateVoiceUi();
      return;
    }

    bgmEngine.ensureContext();
    bgmEngine.ensureRunning();

    if (
      bgmOn &&
      currentBgm &&
      !bgmEngine.hasActiveSource()
    ) {
      bgmEngine.play(
        currentBgm,
        true
      );
    }

    // マイクを開いているだけではBGMを止めない。
    // 発話検知(onspeechstart)が来た瞬間だけ0%へ下げる。
    voiceSpeechDetected = false;
    refreshBgmVolume();

    try {
      voiceRecognition.start();
    } catch (error) {
      voiceRecognitionRunning = false;
      voiceSpeechDetected = false;
      refreshBgmVolume();
      scheduleSilentVoiceRestart(
        500
      );
    }
  }

  function hideQuestionForVoiceCallout() {
    questionCard.classList.add(
      "voice-waiting"
    );
  }

  function revealQuestionAfterVoiceCallout() {
    questionCard.classList.remove(
      "voice-waiting"
    );

    // 表示された瞬間にレイアウトを確定。
    void questionCard.offsetWidth;
  }

  function speakGowasanThenListen() {
    if (
      !voiceAnswerEnabled ||
      voicePauseForFeedback ||
      voiceCalloutRunning ||
      locked ||
      voiceRecognitionRunning ||
      results.classList.contains("show") ||
      !workspace.classList.contains("show")
    ) {
      return;
    }

    voiceCalloutRunning = true;
    gowasanSpokenForIndex =
      index;

    // 表示はカタカナで固定。
    hideQuestionForVoiceCallout();

    updateVoiceUi(
      "音声回答：ゴワサン！"
    );

    voicePauseForFeedback = true;
    refreshBgmVolume();
    bgmEngine.ensureRunning();

    const beginListening = () => {
      voiceCalloutRunning = false;
      voicePauseForFeedback = false;

      if (
        !voiceAnswerEnabled ||
        locked ||
        results.classList.contains("show")
      ) {
        refreshBgmVolume();
        return;
      }

      // ゴワサンの語尾が回答用マイクへ入らないよう、短い無音時間を置く。
      bgmEngine.setVolume(
        BGM_VOLUME_LISTENING,
        0.06
      );

      updateVoiceUi(
        "音声回答：まもなく問題スタート"
      );

      setTimeout(() => {
        if (
          !voiceAnswerEnabled ||
          locked ||
          results.classList.contains("show")
        ) {
          refreshBgmVolume();
          return;
        }

        voiceSpeechDetected = false;
        refreshBgmVolume();
        startMicrophoneRecognition();
      }, MIC_PRE_SILENCE_MS);
    };

    if (
      !("speechSynthesis" in window) ||
      typeof SpeechSynthesisUtterance ===
        "undefined"
    ) {
      beginListening();
      return;
    }

    window.speechSynthesis.cancel();

    // Chrome/Androidでは getVoices() の準備が遅れることがある。
    // 日本語音声が得られてから発声し、英語系フォールバックを避ける。
    waitForJapaneseVoice(
      1400
    ).then((voice) => {
      if (
        !voiceAnswerEnabled ||
        locked ||
        results.classList.contains("show")
      ) {
        voiceCalloutRunning = false;
        voicePauseForFeedback = false;
        refreshBgmVolume();
        return;
      }

      const utterance =
        new SpeechSynthesisUtterance(
          "ゴワサン！"
        );

      utterance.lang =
        "ja-JP";

      // 速すぎず、ピッチを上げず、日本語の短い掛け声として発声。
      utterance.rate =
        0.92;

      utterance.pitch =
        1.00;

      utterance.volume =
        1.0;

      if (voice) {
        utterance.voice =
          voice;
      }

      let finished = false;

      const done = () => {
        if (finished) {
          return;
        }

        finished = true;
        beginListening();
      };

      utterance.onend =
        done;

      utterance.onerror =
        done;

      window.speechSynthesis.speak(
        utterance
      );
    }).catch(() => {
      // 音声取得に失敗してもゲームは止めない。
      beginListening();
    });
  }

  function startVoiceRecognition() {
    // 同じ問題ですでに「ゴワサン」を言っていたら、
    // どこから呼ばれても二度と言わず、マイクだけ再開する。
    if (
      gowasanSpokenForIndex ===
      index
    ) {
      scheduleSilentVoiceRestart(
        120
      );
      return;
    }

    speakGowasanThenListen();
  }

  function setVoiceAnswerEnabled(
    enabled
  ) {
    voiceAnswerEnabled =
      Boolean(enabled);

    if (voiceAnswerEnabled) {
      bgmEngine.ensureContext();
      bgmEngine.ensureRunning();

      if (
        bgmOn &&
        currentBgm &&
        !bgmEngine.hasActiveSource()
      ) {
        bgmEngine.play(
          currentBgm,
          true
        );
      }

      startBgmKeepAlive();

      updateVoiceUi(
        "音声回答：開始します…"
      );

      // ボタンをONにした直後から問題を隠す。
      hideQuestionForVoiceCallout();

      startVoiceRecognition();
      return;
    }

    voiceCalloutRunning = false;
    voicePauseForFeedback = false;

    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    stopVoiceRecognition();
    stopBgmKeepAlive();

    refreshBgmVolume();

    // チャレンジ画面を開いている時だけ問題表示を更新する。
    if (
      workspace.classList.contains("show") &&
      currentMode
    ) {
      renderQuestion(true);
    }

    updateVoiceUi(
      "音声回答：OFF"
    );
  }


  function getJapaneseVoice() {
    if (!("speechSynthesis" in window)) {
      return null;
    }

    const voices =
      window.speechSynthesis.getVoices();

    const japaneseVoices =
      voices.filter((voice) =>
        String(
          voice.lang || ""
        )
          .toLowerCase()
          .startsWith("ja")
      );

    if (
      japaneseVoices.length === 0
    ) {
      return null;
    }

    // 端末に複数音声がある場合は、日本語専用音声を最優先する。
    // Android Chrome では "Google 日本語" が自然な場合が多い。
    const preferredNames = [
      "google 日本語",
      "google japanese",
      "日本語",
      "japanese",
      "nanami",
      "haruka",
      "sayaka",
      "kyoko",
      "otoya",
      "ichiro"
    ];

    const scored =
      japaneseVoices.map(
        (voice) => {
          const name =
            String(
              voice.name || ""
            ).toLowerCase();

          const lang =
            String(
              voice.lang || ""
            ).toLowerCase();

          let score = 0;

          if (
            lang === "ja-jp"
          ) {
            score += 300;
          }

          preferredNames.forEach(
            (preferred, idx) => {
              if (
                name.includes(
                  preferred.toLowerCase()
                )
              ) {
                score +=
                  220 - idx * 8;
              }
            }
          );

          if (
            voice.localService
          ) {
            score += 25;
          }

          // 日本語候補に入っていても名前に英語系表記が混ざるものは避ける。
          if (
            /english|en-us|en-gb|american|british/.test(
              name
            )
          ) {
            score -= 500;
          }

          return {
            voice,
            score
          };
        }
      );

    scored.sort(
      (a, b) =>
        b.score - a.score
    );

    return (
      scored[0]?.voice ||
      japaneseVoices[0]
    );
  }

  function waitForJapaneseVoice(
    timeoutMs = 1400
  ) {
    return new Promise((resolve) => {
      if (
        !("speechSynthesis" in window)
      ) {
        resolve(null);
        return;
      }

      const immediate =
        getJapaneseVoice();

      if (immediate) {
        resolve(immediate);
        return;
      }

      let finished = false;
      const startedAt =
        performance.now();

      const finish = (voice) => {
        if (finished) {
          return;
        }

        finished = true;

        try {
          window.speechSynthesis.removeEventListener(
            "voiceschanged",
            onVoicesChanged
          );
        } catch (error) {}

        resolve(
          voice || null
        );
      };

      const check = () => {
        const voice =
          getJapaneseVoice();

        if (voice) {
          finish(voice);
          return;
        }

        if (
          performance.now() -
            startedAt >=
          timeoutMs
        ) {
          finish(null);
          return;
        }

        setTimeout(
          check,
          80
        );
      };

      const onVoicesChanged = () => {
        const voice =
          getJapaneseVoice();

        if (voice) {
          finish(voice);
        }
      };

      try {
        window.speechSynthesis.addEventListener(
          "voiceschanged",
          onVoicesChanged
        );
      } catch (error) {}

      check();
    });
  }

  function speakAnswerFeedback(
    ok,
    streak
  ) {
    // 判定音声中はマイクだけ一時停止。
    // BGM再生ノードは止めず、読み上げ用の3%へ下げる。
    voicePauseForFeedback = true;

    stopVoiceRecognition();
    refreshBgmVolume();

    bgmEngine.ensureRunning();

    if (
      !("speechSynthesis" in window) ||
      typeof SpeechSynthesisUtterance ===
        "undefined"
    ) {
      voicePauseForFeedback = false;
      refreshBgmVolume();

      if (voiceAnswerEnabled) {
        scheduleVoiceRestart(280);
      }

      return;
    }

    window.speechSynthesis.cancel();

    const message =
      !ok
        ? "残念！"
        : streak >= 2
          ? `お見事！${streak}問連続正解！`
          : "お見事！";

    const utterance =
      new SpeechSynthesisUtterance(
        message
      );

    utterance.lang = "ja-JP";
    utterance.rate = 0.98;
    utterance.pitch = 1.03;
    utterance.volume = 1.0;

    const voice =
      getJapaneseVoice();

    if (voice) {
      utterance.voice = voice;
    }

    let resumed = false;

    const resumeAfterFeedback = () => {
      if (resumed) {
        return;
      }

      resumed = true;
      voicePauseForFeedback = false;

      // BGMはここでも再スタートしない。
      // 同じWebAudioノードのGainだけ戻す。
      refreshBgmVolume();
      bgmEngine.ensureRunning();

      if (voiceAnswerEnabled) {
        scheduleVoiceRestart(280);
      }
    };

    utterance.onend =
      resumeAfterFeedback;

    utterance.onerror =
      resumeAfterFeedback;

    window.speechSynthesis.speak(
      utterance
    );
  }

  function setRainbowForStreak(streak) {
    const vars = [
      "--rb1", "--rb2", "--rb3", "--rb4",
      "--rb5", "--rb6", "--rb7", "--rb8"
    ];

    if (streak < 6) {
      vars.forEach((name) =>
        questionCard.style.removeProperty(name)
      );
      return;
    }

    // 6問連続正解以降は、正解するたび少しずつ配色を変える。
    const baseHue =
      ((streak - 6) * 47) % 360;

    const offsets = [
      0, 46, 92, 138,
      184, 230, 276, 322
    ];

    offsets.forEach(
      (offset, index) => {
        const hue =
          (baseHue + offset) % 360;

        questionCard.style.setProperty(
          `--rb${index + 1}`,
          `hsl(${hue} 92% 58%)`
        );
      }
    );
  }

  function updateStreakFrame(streak) {
    questionCard.classList.remove(
      "streak5",
      "streak6",
      "streak10",
      "question-flash"
    );

    questionCard.style.borderColor = "";
    questionCard.style.borderWidth = "";
    questionCard.style.boxShadow = "";

    setRainbowForStreak(streak);

    if (streak >= 10) {
      questionCard.classList.add(
        "streak10"
      );
      return;
    }

    if (streak >= 6) {
      questionCard.classList.add(
        "streak6"
      );
    }
  }

  function flashQuestionFrameIfNeeded() {
    questionCard.classList.remove(
      "question-flash"
    );

    if (correctStreak < 10) {
      return;
    }

    // reflowして、毎問きちんと発光アニメーションを再生。
    void questionCard.offsetWidth;

    questionCard.classList.add(
      "question-flash"
    );

    setTimeout(() => {
      questionCard.classList.remove(
        "question-flash"
      );
    }, 1200);
  }

  function loadTutorialProgress() {
    try {
      const raw =
        localStorage.getItem(
          LEARNING_PROGRESS_STORAGE_KEY
        );

      if (!raw) {
        return {
          cleared: {}
        };
      }

      const parsed =
        JSON.parse(raw);

      if (
        !parsed ||
        typeof parsed !== "object"
      ) {
        return {
          cleared: {}
        };
      }

      if (
        !parsed.cleared ||
        typeof parsed.cleared !== "object"
      ) {
        parsed.cleared = {};
      }

      return parsed;
    } catch (error) {
      return {
        cleared: {}
      };
    }
  }

  function saveTutorialProgress() {
    try {
      localStorage.setItem(
        LEARNING_PROGRESS_STORAGE_KEY,
        JSON.stringify(
          learningProgress
        )
      );
    } catch (error) {}
  }

  function allTutorialLessons() {
    return Object.values(
      learningCategories
    ).flatMap(
      (category) =>
        category.lessons
    );
  }

  function isTutorialLessonCleared(
    lessonId
  ) {
    return Boolean(
      learningProgress
        .cleared[lessonId]
    );
  }

  function tutorialCategoryStats(
    categoryKey
  ) {
    const lessons =
      learningCategories[
        categoryKey
      ].lessons;

    const cleared =
      lessons.filter(
        (lesson) =>
          isTutorialLessonCleared(
            lesson.id
          )
      ).length;

    return {
      cleared,
      total: lessons.length
    };
  }

  function tutorialLessonUnlocked(
    categoryKey,
    index
  ) {
    if (index === 0) {
      return true;
    }

    const lessons =
      learningCategories[
        categoryKey
      ].lessons;

    return isTutorialLessonCleared(
      lessons[index - 1].id
    );
  }

  function updateTutorialOverallProgress() {
    const all =
      allTutorialLessons();

    const cleared =
      all.filter(
        (lesson) =>
          isTutorialLessonCleared(
            lesson.id
          )
      ).length;

    const total =
      all.length;

    learningOverallText.textContent =
      `全体 ${cleared} / ${total} STEP クリア`;

    learningOverallBar.style.width =
      `${total ? (cleared / total) * 100 : 0}%`;
  }

  function runUiCleanup(callback) {
    try {
      callback();
    } catch (error) {
      // Do not allow cleanup failures to block navigation.
    }
  }

  function forceHomeView() {
    workspace.classList.remove("show");
    results.classList.remove("show");
    menuView.classList.add("hidden");
    learningView.classList.remove("show");
    anzanView.classList.remove("show");
    settingsView.classList.remove("show");
    homeView.classList.remove("hidden");
  }

  function forceChallengeMenuView() {
    homeView.classList.add("hidden");
    learningView.classList.remove("show");
    anzanView.classList.remove("show");
    settingsView.classList.remove("show");
    workspace.classList.remove("show");
    results.classList.remove("show");
    menuView.classList.remove("hidden");
  }

  function showHome() {
    // First switch the view. Camera/audio cleanup happens afterwards.
    forceHomeView();

    runUiCleanup(discardChallengeVideoSession);
    runUiCleanup(stopTimer);
    runUiCleanup(stopBgm);
    runUiCleanup(hideSorobanHint);
    runUiCleanup(stopLearningAnimation);
    runUiCleanup(stopAnzanSession);
    runUiCleanup(() => setVoiceAnswerEnabled(false));
    runUiCleanup(closeRankingModal);
    runUiCleanup(closeRankCelebration);
    runUiCleanup(updateRankUi);

    currentMode = null;
  }

  function showChallengeMenu() {
    forceChallengeMenuView();

    runUiCleanup(stopLearningAnimation);
    runUiCleanup(stopAnzanSession);
    runUiCleanup(stopTimer);
    runUiCleanup(stopBgm);
    runUiCleanup(hideSorobanHint);
    runUiCleanup(() => setVoiceAnswerEnabled(false));
    runUiCleanup(closeRankCelebration);
  }

  function showLearningHome() {
    stopTimer();
    stopBgm();
    hideSorobanHint();
    setVoiceAnswerEnabled(false);

    homeView.classList.add(
      "hidden"
    );
    menuView.classList.add(
      "hidden"
    );
    anzanView.classList.remove(
      "show"
    );
    settingsView.classList.remove(
      "show"
    );
    workspace.classList.remove(
      "show"
    );
    results.classList.remove(
      "show"
    );

    learningView.classList.add(
      "show"
    );

    learningCategoryView.classList.remove(
      "hidden"
    );
    learningStageView.classList.add(
      "hidden"
    );
    learningLessonView.classList.add(
      "hidden"
    );

    learningCurrentCategory = null;
    stopLearningAnimation();
    renderLearningCategories();
  }

  function renderLearningCategories() {
    learningCategoryGrid.innerHTML =
      "";

    updateTutorialOverallProgress();

    Object.entries(
      learningCategories
    ).forEach(
      ([key, category]) => {
        const stats =
          tutorialCategoryStats(
            key
          );

        const card =
          document.createElement(
            "article"
          );

        card.className =
          "learning-category-card";

        const starsFilled =
          "★".repeat(
            stats.cleared
          );

        const starsEmpty =
          "☆".repeat(
            stats.total -
            stats.cleared
          );

        card.innerHTML = `
          <div class="learning-category-symbol">${category.symbol}</div>
          <div class="learning-category-title">${category.title}</div>
          <div class="learning-category-desc">${category.desc}</div>
          <div class="learning-stars">${starsFilled}${starsEmpty} ${stats.cleared}/${stats.total}</div>
        `;

        const button =
          document.createElement(
            "button"
          );

        button.type =
          "button";

        button.className =
          "primary";

        button.textContent =
          stats.cleared === 0
            ? "STEP 1から学ぶ"
            : stats.cleared === stats.total
              ? "復習する"
              : "続きから学ぶ";

        button.addEventListener(
          "click",
          () => {
            showLearningStages(
              key
            );
          }
        );

        card.appendChild(button);
        learningCategoryGrid.appendChild(
          card
        );
      }
    );
  }

  function showLearningStages(
    categoryKey
  ) {
    learningCurrentCategory =
      categoryKey;

    stopLearningAnimation();

    learningCategoryView.classList.add(
      "hidden"
    );

    learningLessonView.classList.add(
      "hidden"
    );

    learningStageView.classList.remove(
      "hidden"
    );

    const category =
      learningCategories[
        categoryKey
      ];

    learningStageKicker.textContent =
      `${category.symbol} ${category.title}`;

    learningStageHeading.textContent =
      "少しずつSTEP UP";

    learningStageGrid.innerHTML =
      "";

    category.lessons.forEach(
      (lesson, index) => {
        const unlocked =
          tutorialLessonUnlocked(
            categoryKey,
            index
          );

        const cleared =
          isTutorialLessonCleared(
            lesson.id
          );

        const card =
          document.createElement(
            "article"
          );

        card.className =
          "learning-stage-card";

        if (!unlocked) {
          card.classList.add(
            "locked"
          );
        }

        if (cleared) {
          card.classList.add(
            "cleared"
          );
        }

        card.innerHTML = `
          <div class="learning-stage-no">STEP ${index + 1}</div>
          <div class="learning-stage-title">${lesson.title}</div>
          <div class="learning-stage-desc">${lesson.desc}</div>
          <div class="learning-stage-status">${
            cleared
              ? "★ クリア"
              : unlocked
                ? "○ 学習できます"
                : "🔒 1つ前のSTEPをクリアすると開きます"
          }</div>
        `;

        const button =
          document.createElement(
            "button"
          );

        button.type =
          "button";

        button.disabled =
          !unlocked;

        button.textContent =
          cleared
            ? "もう一度見る"
            : unlocked
              ? "学習スタート"
              : "ロック中";

        if (unlocked) {
          button.className =
            "primary";

          button.addEventListener(
            "click",
            () => {
              openLearningLesson(
                categoryKey,
                index
              );
            }
          );
        }

        card.appendChild(
          button
        );

        learningStageGrid.appendChild(
          card
        );
      }
    );
  }

  function buildLearningBoard(
    digitCount
  ) {
    learningRods = [];
    learningSorobanBoard.innerHTML =
      "";

    const safeCount =
      Math.max(
        1,
        digitCount
      );

    for (
      let i = 0;
      i < safeCount;
      i += 1
    ) {
      const place =
        10 ** (
          safeCount -
          1 -
          i
        );

      const rod =
        document.createElement(
          "div"
        );

      rod.className =
        "soroban-rod";

      rod.dataset.place =
        String(place);

      const line =
        document.createElement(
          "div"
        );

      line.className =
        "rod-line";

      rod.appendChild(
        line
      );

      const upper =
        document.createElement(
          "div"
        );

      upper.className =
        "soroban-bead upper-bead";

      rod.appendChild(
        upper
      );

      const lowers = [];

      for (
        let j = 0;
        j < 4;
        j += 1
      ) {
        const bead =
          document.createElement(
            "div"
          );

        bead.className =
          "soroban-bead lower-bead";

        bead.style.top =
          `${112 + j * 18}px`;

        rod.appendChild(
          bead
        );

        lowers.push(
          bead
        );
      }

      const label =
        document.createElement(
          "div"
        );

      label.className =
        "rod-place";

      label.textContent =
        shortPlaceName(
          place
        );

      rod.appendChild(
        label
      );

      learningSorobanBoard.appendChild(
        rod
      );

      learningRods.push({
        rod,
        place,
        upper,
        lowers
      });
    }
  }

  function showLearningNumber(
    value,
    activePlace = null
  ) {
    const safeValue =
      Math.max(
        0,
        Math.floor(
          Math.abs(
            Number(value) || 0
          )
        )
      );

    const digits =
      String(safeValue)
        .padStart(
          learningRods.length,
          "0"
        )
        .slice(
          -learningRods.length
        )
        .split("")
        .map(Number);

    learningRods.forEach(
      (rodInfo, index) => {
        setRodDigit(
          rodInfo,
          digits[index]
        );

        rodInfo.rod
          .classList.toggle(
            "active",
            activePlace !== null &&
            rodInfo.place ===
              activePlace
          );
      }
    );
  }

  function currentLearningLesson() {
    if (!learningCurrentCategory) {
      return null;
    }

    return learningCategories[
      learningCurrentCategory
    ].lessons[
      learningCurrentLessonIndex
    ] || null;
  }

  function updateLearningVoiceUi() {
    learningVoiceBtn.textContent =
      learningVoiceEnabled
        ? "🔊 音声解説 ON"
        : "🔊 音声解説 OFF";

    learningVoiceBtn.classList.toggle(
      "on",
      learningVoiceEnabled
    );

    learningVoiceNote.textContent =
      learningVoiceEnabled
        ? "音声解説：ON　ひとつずつ、ゆっくり説明します。"
        : "音声解説をONにすると、珠の動きをゆっくり読み上げます。";
  }

  function stopLearningNarration() {
    learningNarrationToken += 1;

    if (
      "speechSynthesis" in window
    ) {
      window.speechSynthesis.cancel();
    }
  }

  function learningNarrationText(
    lesson,
    frameIndex
  ) {
    const scripts =
      learningVoiceScripts[
        lesson.id
      ];

    if (
      Array.isArray(scripts) &&
      scripts[frameIndex]
    ) {
      return scripts[
        frameIndex
      ];
    }

    return String(
      lesson.frames[
        frameIndex
      ]?.text || ""
    );
  }

  function speakLearningFrame(
    lesson,
    frameIndex
  ) {
    if (!learningVoiceEnabled) {
      return;
    }

    if (
      !("speechSynthesis" in window) ||
      typeof SpeechSynthesisUtterance ===
        "undefined"
    ) {
      learningVoiceNote.textContent =
        "このブラウザでは音声解説を使えません。";
      return;
    }

    const message =
      learningNarrationText(
        lesson,
        frameIndex
      );

    if (!message) {
      return;
    }

    // 前の説明が残っていたら止めて、
    // 現在の珠の動きだけを説明する。
    window.speechSynthesis.cancel();

    const token =
      ++learningNarrationToken;

    const utterance =
      new SpeechSynthesisUtterance(
        message
      );

    utterance.lang = "ja-JP";

    // 子どもが珠を見ながら追えるよう、
    // 通常の読み上げよりかなりゆっくり。
    utterance.rate = 0.72;
    utterance.pitch = 1.18;
    utterance.volume = 1.0;

    const voice =
      getJapaneseVoice();

    if (voice) {
      utterance.voice = voice;
    }

    utterance.onend = () => {
      if (
        token !==
        learningNarrationToken
      ) {
        return;
      }
    };

    utterance.onerror =
      utterance.onend;

    window.speechSynthesis.speak(
      utterance
    );
  }

  function setLearningVoiceEnabled(
    enabled,
    savePreference = true
  ) {
    learningVoiceEnabled =
      Boolean(enabled);

    if (savePreference) {
      preferredLearningVoiceEnabled =
        learningVoiceEnabled;

      saveBooleanSetting(
        LEARNING_VOICE_SETTING_KEY,
        preferredLearningVoiceEnabled
      );

      updateSettingsUi();
    }

    stopLearningNarration();
    updateLearningVoiceUi();

    if (
      learningVoiceEnabled &&
      currentLearningLesson()
    ) {
      // ONにした瞬間から説明と珠の動きをそろえるため、
      // 動画を先頭から再生する。
      runLearningAnimation(
        true
      );
    }
  }

  function stopLearningAnimation() {
    learningAnimationToken += 1;
    learningAnimationPaused = false;
    stopLearningNarration();
  }

  function renderLearningAnimationState(
    lesson,
    frameIndex
  ) {
    const lastIndex =
      lesson.frames.length - 1;

    const isStart =
      frameIndex === 0;

    const isAnswer =
      frameIndex === lastIndex &&
      lastIndex > 0;

    learningVideoCard.classList.remove(
      "is-active",
      "is-answer"
    );

    if (isAnswer) {
      learningVideoCard.classList.add(
        "is-answer"
      );
    } else if (!isStart) {
      learningVideoCard.classList.add(
        "is-active"
      );
    }

    // ループ先頭へ戻ったら、必ず「?」へ戻す。
    if (!isAnswer) {
      learningExampleProblem.textContent =
        lesson.example || "";
      return;
    }

    // 完成形では「?」だけを赤い答えへ置き換える。
    const answer =
      String(
        lesson.frames[lastIndex].value
      );

    const example =
      String(
        lesson.example || ""
      );

    const questionMarkIndex =
      example.lastIndexOf("?");

    if (questionMarkIndex < 0) {
      learningExampleProblem.textContent =
        `${example} ${answer}`;
      return;
    }

    const before =
      example.slice(
        0,
        questionMarkIndex
      );

    const after =
      example.slice(
        questionMarkIndex + 1
      );

    learningExampleProblem.innerHTML =
      `${before}<span class="learning-answer-red">${answer}</span>${after}`;
  }

  function renderLearningFrame(
    frameIndex
  ) {
    const lesson =
      currentLearningLesson();

    if (!lesson) {
      return;
    }

    const safeIndex =
      Math.max(
        0,
        Math.min(
          lesson.frames.length - 1,
          frameIndex
        )
      );

    const frame =
      lesson.frames[
        safeIndex
      ];

    learningCurrentFrame =
      safeIndex;

    learningStepText.textContent =
      frame.text;

    learningFrameCounter.textContent =
      `${safeIndex + 1} / ${lesson.frames.length}`;

    renderLearningAnimationState(
      lesson,
      safeIndex
    );

    showLearningNumber(
      frame.value,
      frame.place
    );

    speakLearningFrame(
      lesson,
      safeIndex
    );
  }

  function runLearningAnimation(
    restart = true
  ) {
    const lesson =
      currentLearningLesson();

    if (!lesson) {
      return;
    }

    if (restart) {
      stopLearningAnimation();
      learningCurrentFrame = 0;
    }

    const token =
      learningAnimationToken;

    learningAnimationPaused =
      false;

    learningPauseBtn.textContent =
      "Ⅱ 一時停止";

    const showNext = () => {
      if (
        token !==
        learningAnimationToken
      ) {
        return;
      }

      if (
        learningAnimationPaused
      ) {
        setTimeout(
          showNext,
          180
        );
        return;
      }

      const frameBeingShown =
        learningCurrentFrame;

      renderLearningFrame(
        frameBeingShown
      );

      learningCurrentFrame += 1;

      if (
        learningCurrentFrame >=
        lesson.frames.length
      ) {
        learningCurrentFrame = 0;

        // 完成形を十分見せる。
        // 音声ONのときは、読み上げが終わる時間も確保する。
        const answerDelay =
          learningVoiceEnabled
            ? 7200
            : 3200;

        setTimeout(
          showNext,
          answerDelay
        );
        return;
      }

      // 音声OFFはこれまでのゆっくり速度。
      // 音声ONは、短い解説を聞き終えてから次へ進む。
      const frameDelay =
        learningVoiceEnabled
          ? (
              frameBeingShown === 0
                ? 7600
                : 6800
            )
          : (
              frameBeingShown === 0
                ? 3800
                : 3200
            );

      setTimeout(
        showNext,
        frameDelay
      );
    };

    showNext();
  }

  function renderLearningQuiz(
    lesson
  ) {
    learningQuizQuestion.textContent =
      lesson.quiz.q;

    learningQuizOptions.innerHTML =
      "";

    learningQuizFeedback.textContent =
      "";

    learningClearActions.classList.remove(
      "show"
    );

    lesson.quiz.options.forEach(
      (option) => {
        const button =
          document.createElement(
            "button"
          );

        button.type =
          "button";

        button.className =
          "learning-quiz-option";

        button.textContent =
          option;

        button.addEventListener(
          "click",
          () => {
            const correct =
              option ===
              lesson.quiz.answer;

            learningQuizOptions
              .querySelectorAll(
                "button"
              )
              .forEach(
                (item) => {
                  item.disabled =
                    true;

                  if (
                    item.textContent ===
                    lesson.quiz.answer
                  ) {
                    item.classList.add(
                      "correct-choice"
                    );
                  }
                }
              );

            if (correct) {
              learningProgress
                .cleared[
                  lesson.id
                ] = true;

              saveTutorialProgress();

              learningQuizFeedback
                .textContent =
                "★ クリア！ 次のSTEPが開きました。";

              learningQuizFeedback
                .style.color =
                "#2f6a43";

              learningClearActions
                .classList.add(
                  "show"
                );

              renderLearningCategories();
            } else {
              button.classList.add(
                "wrong-choice"
              );

              learningQuizFeedback
                .textContent =
                `もう一度動画を見てみよう。正解は「${lesson.quiz.answer}」。`;

              learningQuizFeedback
                .style.color =
                "#a14428";

              learningClearActions
                .classList.add(
                  "show"
                );
            }
          }
        );

        learningQuizOptions
          .appendChild(
            button
          );
      }
    );
  }

  function openLearningLesson(
    categoryKey,
    lessonIndex
  ) {
    learningCurrentCategory =
      categoryKey;

    learningCurrentLessonIndex =
      lessonIndex;

    const category =
      learningCategories[
        categoryKey
      ];

    const lesson =
      category.lessons[
        lessonIndex
      ];

    learningCategoryView
      .classList.add(
        "hidden"
      );

    learningStageView
      .classList.add(
        "hidden"
      );

    learningLessonView
      .classList.remove(
        "hidden"
      );

    learningLessonKicker.textContent =
      `${category.symbol} ${category.title} / STEP ${lessonIndex + 1}`;

    learningLessonTitle.textContent =
      lesson.title;

    learningVoiceEnabled =
      preferredLearningVoiceEnabled;

    updateLearningVoiceUi();

    learningExampleProblem.textContent =
      lesson.example || "";

    learningVideoCard.classList.remove(
      "is-active",
      "is-answer"
    );

    learningBasicText.textContent =
      lesson.basic;

    learningTroubleText.textContent =
      lesson.trouble;

    learningRuleBox.textContent =
      lesson.rule;

    const maxValue =
      Math.max(
        0,
        ...lesson.frames.map(
          (frame) =>
            Math.abs(
              Number(
                frame.value
              ) || 0
            )
        )
      );

    const digitCount =
      Math.max(
        1,
        String(
          Math.floor(
            maxValue
          )
        ).length
      );

    buildLearningBoard(
      digitCount
    );

    renderLearningQuiz(
      lesson
    );

    renderLearningFrame(
      0
    );

    runLearningAnimation(
      true
    );

    const nextExists =
      Boolean(
        category.lessons[
          lessonIndex + 1
        ]
      );

    learningNextBtn.textContent =
      nextExists
        ? "次のSTEPへ"
        : "STEP一覧へ";
  }

  function goNextLearningLesson() {
    const category =
      learningCategories[
        learningCurrentCategory
      ];

    const nextIndex =
      learningCurrentLessonIndex +
      1;

    if (
      category &&
      category.lessons[
        nextIndex
      ] &&
      tutorialLessonUnlocked(
        learningCurrentCategory,
        nextIndex
      )
    ) {
      openLearningLesson(
        learningCurrentCategory,
        nextIndex
      );
      return;
    }

    showLearningStages(
      learningCurrentCategory
    );
  }

  function stopAnzanRecognition() {
    if (!anzanRecognition) {
      anzanListening = false;
      anzanListeningStartedAt = 0;
      anzanSpeechDetected = false;
      refreshBgmVolume();
      return;
    }

    try {
      anzanRecognition.onend = null;
      anzanRecognition.abort();
    } catch (error) {}

    anzanRecognition = null;
    anzanListening = false;
    anzanListeningStartedAt = 0;
    anzanSpeechDetected = false;
    refreshBgmVolume();
  }

  function stopAnzanSession() {
    anzanRunToken += 1;
    stopAnzanRecognition();
    anzanLocked = true;
  }

  function showAnzanHome() {
    stopTimer();
    stopBgm();
    hideSorobanHint();
    stopLearningAnimation();
    stopAnzanSession();

    homeView.classList.add(
      "hidden"
    );
    menuView.classList.add(
      "hidden"
    );
    learningView.classList.remove(
      "show"
    );
    settingsView.classList.remove(
      "show"
    );
    workspace.classList.remove(
      "show"
    );
    results.classList.remove(
      "show"
    );

    anzanView.classList.add(
      "show"
    );
    anzanMenuView.classList.remove(
      "hidden"
    );
    anzanGameView.classList.add(
      "hidden"
    );
    anzanResultView.classList.add(
      "hidden"
    );
  }

  function showSettings() {
    stopTimer();
    stopBgm();
    hideSorobanHint();
    stopLearningAnimation();
    stopAnzanSession();

    homeView.classList.add(
      "hidden"
    );
    menuView.classList.add(
      "hidden"
    );
    learningView.classList.remove(
      "show"
    );
    anzanView.classList.remove(
      "show"
    );
    workspace.classList.remove(
      "show"
    );
    results.classList.remove(
      "show"
    );

    settingsView.classList.add(
      "show"
    );

    updateSettingsUi();
  }

  function makeAnzanQuestion(
    level
  ) {
    let terms = [];
    let total = 0;

    if (level === 1) {
      const count = 3;

      for (
        let i = 0;
        i < count;
        i += 1
      ) {
        const value =
          randomInt(
            1,
            9
          );

        terms.push({
          op: i === 0
            ? ""
            : "＋",
          value
        });

        total += value;
      }

      return {
        terms,
        answer: total
      };
    }

    const count = 5;
    const minValue =
      level === 2
        ? 1
        : 10;
    const maxValue =
      level === 2
        ? 9
        : 49;

    const first =
      randomInt(
        level === 2
          ? 4
          : 25,
        level === 2
          ? 9
          : 49
      );

    terms.push({
      op: "",
      value: first
    });

    total = first;

    for (
      let i = 1;
      i < count;
      i += 1
    ) {
      let op = "＋";
      let value =
        randomInt(
          minValue,
          maxValue
        );

      if (
        Math.random() < 0.45 &&
        total > minValue
      ) {
        const maxSubtract =
          Math.min(
            maxValue,
            total
          );

        if (
          maxSubtract >= minValue
        ) {
          op = "－";
          value =
            randomInt(
              minValue,
              maxSubtract
            );
        }
      }

      if (op === "＋") {
        total += value;
      } else {
        total -= value;
      }

      terms.push({
        op,
        value
      });
    }

    return {
      terms,
      answer: total
    };
  }

  function updateAnzanAnswerDisplay() {
    anzanAnswerDisplay.textContent =
      anzanAnswerBuffer ||
      "\u00a0";
  }

  function startAnzanMode(
    level
  ) {
    stopAnzanSession();

    anzanLevel =
      Number(level) || 1;

    anzanQuestionIndex = 0;
    anzanCorrect = 0;
    anzanLocked = false;

    anzanMenuView.classList.add(
      "hidden"
    );
    anzanResultView.classList.add(
      "hidden"
    );
    anzanGameView.classList.remove(
      "hidden"
    );

    anzanSpeedLabel.textContent =
      anzanSpeedInfo().label;

    runAnzanQuestion();
  }

  function runAnzanQuestion() {
    stopAnzanRecognition();

    const token =
      ++anzanRunToken;

    anzanLocked = true;
    anzanAnswerBuffer = "";
    updateAnzanAnswerDisplay();

    anzanQuestion =
      makeAnzanQuestion(
        anzanLevel
      );

    anzanQCount.textContent =
      `${anzanQuestionIndex + 1} / ${ANZAN_TOTAL_QUESTIONS}`;

    anzanCorrectCount.textContent =
      `正解 ${anzanCorrect}`;

    anzanFeedback.textContent =
      "";

    anzanFeedback.className =
      "anzan-feedback";

    anzanAnswerArea.classList.add(
      "hidden"
    );

    anzanFlash.className =
      "anzan-flash ready";

    anzanFlash.textContent =
      "よーい";

    anzanHint.textContent =
      "数字を順番に見てね";

    const info =
      anzanSpeedInfo();

    let termIndex = 0;

    const showTerm = () => {
      if (
        token !== anzanRunToken
      ) {
        return;
      }

      if (
        termIndex >=
        anzanQuestion.terms.length
      ) {
        anzanFlash.className =
          "anzan-flash answer";

        anzanFlash.textContent =
          "＝ ?";

        anzanHint.textContent =
          preferredVoiceAnswerEnabled
            ? "答えを声で言ってね"
            : "答えを入力してね";

        anzanAnswerArea.classList.remove(
          "hidden"
        );

        anzanLocked = false;

        if (
          preferredVoiceAnswerEnabled
        ) {
          // 答え画面になったらそのままマイクを開始。
          // BGMは流したまま、実際の発話時だけ自動で下げる。
          setTimeout(
            () => {
              if (
                !anzanLocked
              ) {
                startAnzanVoiceRecognition();
              }
            },
            180
          );
        }

        return;
      }

      const term =
        anzanQuestion.terms[
          termIndex
        ];

      anzanFlash.className =
        "anzan-flash";

      anzanFlash.textContent =
        `${term.op}${term.value}`;

      termIndex += 1;

      setTimeout(
        showTerm,
        info.delay
      );
    };

    setTimeout(
      showTerm,
      900
    );
  }

  function startAnzanVoiceRecognition() {
    const Recognition =
      getSpeechRecognitionClass();

    if (!Recognition) {
      anzanVoiceStatus.textContent =
        "このブラウザでは音声回答を使えません。数字で答えてください。";
      return;
    }

    stopAnzanRecognition();

    const recognition =
      new Recognition();

    anzanRecognition =
      recognition;

    recognition.lang =
      "ja-JP";

    recognition.continuous =
      false;

    recognition.interimResults =
      false;

    recognition.maxAlternatives =
      5;

    recognition.onstart = () => {
      anzanListening = true;
      anzanListeningStartedAt =
        Date.now();
      anzanSpeechDetected = false;
      refreshBgmVolume();

      anzanVoiceStatus.textContent =
        "🎤 答えを言ってね";
    };

    recognition.onspeechstart = () => {
      anzanSpeechDetected = true;
      refreshBgmVolume();

      anzanVoiceStatus.textContent =
        "🎤 聞いています…";
    };

    recognition.onspeechend = () => {
      anzanVoiceStatus.textContent =
        "🎤 確認中…";
    };

    recognition.onresult =
      (event) => {
        const result =
          event.results?.[0];

        if (
          anzanListeningStartedAt > 0 &&
          Date.now() -
            anzanListeningStartedAt <
            350
        ) {
          anzanVoiceStatus.textContent =
            "🎤 答えを言ってね";
          return;
        }

        const selected =
          selectBestNumericSpeech(
            result
          );

        if (selected) {
          anzanAnswerBuffer =
            String(
              selected.value
            );

          updateAnzanAnswerDisplay();
          submitAnzanAnswer();
        } else {
          anzanVoiceStatus.textContent =
            "数字だけを、もう一度言ってね。";
        }
      };

    recognition.onerror = () => {
      anzanListening = false;
      anzanListeningStartedAt = 0;
      anzanSpeechDetected = false;
      refreshBgmVolume();
      anzanVoiceStatus.textContent =
        "うまく聞き取れませんでした。数字でも答えられます。";
    };

    recognition.onend = () => {
      anzanListening = false;
      anzanListeningStartedAt = 0;
      anzanSpeechDetected = false;
      refreshBgmVolume();
    };

    // 暗算でも、マイクONだけではBGMを止めない。
    // 実際の発話検知時だけ0%へ下げる。
    anzanSpeechDetected = false;
    refreshBgmVolume();

    try {
      recognition.start();
    } catch (error) {
      anzanListening = false;
      refreshBgmVolume();
      anzanVoiceStatus.textContent =
        "音声回答を開始できませんでした。";
    }
  }

  function submitAnzanAnswer() {
    if (
      anzanLocked ||
      !anzanQuestion
    ) {
      return;
    }

    const raw =
      String(
        anzanAnswerBuffer || ""
      ).trim();

    if (!raw) {
      anzanFeedback.textContent =
        "答えを入力してください";
      anzanFeedback.className =
        "anzan-feedback bad";
      return;
    }

    const user =
      Number(raw);

    if (!Number.isFinite(user)) {
      return;
    }

    anzanLocked = true;
    stopAnzanRecognition();

    const ok =
      user ===
      anzanQuestion.answer;

    if (ok) {
      anzanCorrect += 1;
      anzanFeedback.textContent =
        "お見事！";
      anzanFeedback.className =
        "anzan-feedback good";
    } else {
      anzanFeedback.textContent =
        `残念！ 答えは ${anzanQuestion.answer}`;
      anzanFeedback.className =
        "anzan-feedback bad";
    }

    anzanCorrectCount.textContent =
      `正解 ${anzanCorrect}`;

    setTimeout(
      () => {
        anzanQuestionIndex += 1;

        if (
          anzanQuestionIndex >=
          ANZAN_TOTAL_QUESTIONS
        ) {
          finishAnzanMode();
        } else {
          runAnzanQuestion();
        }
      },
      1100
    );
  }

  function finishAnzanMode() {
    stopAnzanSession();

    anzanGameView.classList.add(
      "hidden"
    );
    anzanResultView.classList.remove(
      "hidden"
    );

    anzanResultScore.textContent =
      `${anzanCorrect} / ${ANZAN_TOTAL_QUESTIONS}`;

    anzanResultText.textContent =
      anzanCorrect ===
        ANZAN_TOTAL_QUESTIONS
        ? "全問正解。次のレベルにも挑戦できます。"
        : "同じレベルを繰り返すと、数字を頭に残しやすくなります。";
  }

  function loadBooleanSetting(
    key,
    fallback
  ) {
    try {
      const raw =
        localStorage.getItem(
          key
        );

      if (raw === "true") {
        return true;
      }

      if (raw === "false") {
        return false;
      }
    } catch (error) {}

    return fallback;
  }

  function saveBooleanSetting(
    key,
    value
  ) {
    try {
      localStorage.setItem(
        key,
        String(
          Boolean(value)
        )
      );
    } catch (error) {}
  }

  function loadAnzanSpeed() {
    try {
      const value =
        localStorage.getItem(
          ANZAN_SPEED_SETTING_KEY
        );

      if (
        value === "slow" ||
        value === "normal" ||
        value === "fast"
      ) {
        return value;
      }
    } catch (error) {}

    return "slow";
  }

  function saveAnzanSpeed() {
    try {
      localStorage.setItem(
        ANZAN_SPEED_SETTING_KEY,
        anzanSpeed
      );
    } catch (error) {}
  }

  function anzanSpeedInfo() {
    if (anzanSpeed === "fast") {
      return {
        label: "はやい",
        delay: 850
      };
    }

    if (anzanSpeed === "normal") {
      return {
        label: "ふつう",
        delay: 1350
      };
    }

    return {
      label: "ゆっくり",
      delay: 2000
    };
  }

  function updateSettingsUi() {
    settingTimeValue.textContent =
      timeLimitEnabled
        ? "5分"
        : "時間制限なし";

    settingVoiceValue.textContent =
      preferredVoiceAnswerEnabled
        ? "ON"
        : "OFF";

    settingBgmValue.textContent =
      bgmOn
        ? "ON"
        : "OFF";

    settingLearningVoiceValue.textContent =
      preferredLearningVoiceEnabled
        ? "ON"
        : "OFF";

    settingAnzanSpeedValue.textContent =
      anzanSpeedInfo().label;

    updateRankUi();
  }

  function cycleAnzanSpeed() {
    anzanSpeed =
      anzanSpeed === "slow"
        ? "normal"
        : anzanSpeed === "normal"
          ? "fast"
          : "slow";

    saveAnzanSpeed();
    updateSettingsUi();
  }

  const TIME_LIMIT_STORAGE_KEY =
    "soroban_time_limit_enabled_v1";

  function loadTimeLimitSetting() {
    try {
      const raw =
        localStorage.getItem(
          TIME_LIMIT_STORAGE_KEY
        );

      if (raw === "false") {
        return false;
      }
    } catch (error) {}

    return true;
  }

  function saveTimeLimitSetting() {
    try {
      localStorage.setItem(
        TIME_LIMIT_STORAGE_KEY,
        String(timeLimitEnabled)
      );
    } catch (error) {}
  }

  function updateTimeLimitUi() {
    root.classList.toggle(
      "untimed-mode",
      !timeLimitEnabled
    );

    timeLimitBtn.textContent =
      timeLimitEnabled
        ? "5分"
        : "OFF";

    timeLimitBtn.classList.toggle(
      "off",
      !timeLimitEnabled
    );

    modeTimeBadge.textContent =
      timeLimitEnabled
        ? "12問 / 5分"
        : "12問 / 時間制限なし";

    root
      .querySelectorAll(
        ".time-chip"
      )
      .forEach((chip) => {
        chip.textContent =
          timeLimitEnabled
            ? "5分"
            : "時間制限なし";
      });

    statusSub.textContent =
      timeLimitEnabled
        ? "12問・制限時間5分"
        : "12問・時間制限なし・得点なし";

    if (!workspace.classList.contains("show")) {
      return;
    }

    updateTimer();
  }

  function updateAnalysisVideoUi() {
    if (!analysisVideoToggle) {
      return;
    }

    analysisVideoToggle.textContent =
      analysisVideoEnabled
        ? "ON"
        : "OFF";

    analysisVideoToggle.classList.toggle(
      "on",
      analysisVideoEnabled
    );
  }

  function toggleAnalysisVideo() {
    analysisVideoEnabled =
      !analysisVideoEnabled;

    saveBooleanSetting(
      ANALYSIS_VIDEO_SETTING_KEY,
      analysisVideoEnabled
    );

    updateAnalysisVideoUi();
  }

  function releaseAnalysisCameraStream() {
    if (!analysisCameraStream) {
      if (analysisCameraPreview) {
        analysisCameraPreview.srcObject =
          null;
      }
      return;
    }

    analysisCameraStream
      .getTracks()
      .forEach((track) => {
        try {
          track.stop();
        } catch (error) {}
      });

    analysisCameraStream = null;

    if (analysisCameraPreview) {
      analysisCameraPreview.srcObject =
        null;
    }
  }

  function closeCameraSetup(
    stopStream = true
  ) {
    cameraSetupModal.classList.remove(
      "show"
    );

    root
      .querySelectorAll(
        ".mode-card.camera-setup-active"
      )
      .forEach((card) => {
        card.classList.remove(
          "camera-setup-active"
        );
      });

    if (stopStream) {
      releaseAnalysisCameraStream();
    }

    cameraBeginBtn.disabled = true;
  }

  function revokeSessionVideoUrl() {
    if (sessionVideoUrl) {
      try {
        URL.revokeObjectURL(
          sessionVideoUrl
        );
      } catch (error) {}
    }

    sessionVideoUrl = null;
    sessionVideoBlob = null;

  }

  function resetSessionVideoData() {
    revokeSessionVideoUrl();

    sessionVideoChunks = [];
    sessionVideoMimeType = "";
    sessionVideoStartedAtPerf = null;
    sessionVideoStartedAtIso = null;
    sessionVideoDurationMs = 0;
    sessionVideoActive = false;
    sessionVideoDiscardOnStop = false;
    sessionVideoMode = null;
    sessionQuestionMarks = [];
    reviewClipEndSec = null;

    recordingBadge.classList.remove(
      "show"
    );

    videoReviewPanel.classList.add(
      "hidden"
    );

    if (hintBtn) {
      hintBtn.disabled = false;
      hintBtn.classList.remove(
        "recording-disabled"
      );
      hintBtn.textContent =
        "珠ヒントを見る（手動＋音声）";
    }
  }

  function chooseSessionVideoMimeType() {
    if (
      typeof MediaRecorder ===
      "undefined"
    ) {
      return "";
    }

    const candidates = [
      "video/mp4;codecs=h264",
      "video/mp4",
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm"
    ];

    for (
      const candidate
      of candidates
    ) {
      try {
        if (
          MediaRecorder.isTypeSupported(
            candidate
          )
        ) {
          return candidate;
        }
      } catch (error) {}
    }

    return "";
  }

  async function openCameraSetup(
    mode,
    sourceButton = null
  ) {
    pendingVideoMode =
      mode;

    closeCameraSetup(true);

    // Streamlit components.html は高さの大きい iframe 内で動くため、
    // fixed/vh の全画面モーダルはスマホ実画面と一致しない。
    // 押したモードカードの直下へ設定パネル自体を移動する。
    const sourceCard =
      sourceButton
        ? sourceButton.closest(
            ".mode-card"
          )
        : null;

    let targetCard =
      sourceCard;

    if (!targetCard) {
      const retryButton =
        root.querySelector(
          `.start-mode[data-mode="${mode}"]`
        );

      targetCard =
        retryButton
          ? retryButton.closest(
              ".mode-card"
            )
          : null;
    }

    if (targetCard) {
      targetCard.classList.add(
        "camera-setup-active"
      );

      targetCard.appendChild(
        cameraSetupModal
      );
    }

    cameraSetupModal.classList.add(
      "show"
    );

    cameraBeginBtn.disabled = true;
    cameraSetupStatus.textContent =
      "インカメラを準備しています。ブラウザのカメラ許可をONにしてください。";

    requestAnimationFrame(
      () => {
        try {
          cameraSetupModal.scrollIntoView({
            behavior: "smooth",
            block: "nearest"
          });
        } catch (error) {}
      }
    );

    if (
      !navigator.mediaDevices ||
      !navigator.mediaDevices.getUserMedia ||
      typeof MediaRecorder ===
        "undefined"
    ) {
      cameraSetupStatus.textContent =
        "このブラウザでは動画撮影を開始できません。『撮影なしで開始』は利用できます。";
      return;
    }

    try {
      const stream =
        await navigator.mediaDevices
          .getUserMedia({
            video: {
              facingMode: {
                ideal: "user"
              },
              width: {
                ideal: 1280
              },
              height: {
                ideal: 960
              },
              aspectRatio: {
                ideal: 1.333333
              },
              frameRate: {
                ideal: 20,
                max: 24
              }
            },
            // 音声は録画へ入れない。
            // Web Speech APIの回答用マイクと競合させないため。
            audio: false
          });

      if (
        !cameraSetupModal.classList
          .contains("show")
      ) {
        stream
          .getTracks()
          .forEach((track) => {
            track.stop();
          });
        return;
      }

      analysisCameraStream =
        stream;

      analysisCameraPreview.srcObject =
        stream;

      try {
        await analysisCameraPreview.play();
      } catch (error) {}

      const track =
        stream.getVideoTracks()[0];

      const settings =
        track &&
        typeof track.getSettings ===
          "function"
          ? track.getSettings()
          : {};

      const sizeText =
        settings.width &&
        settings.height
          ? ` ${settings.width}×${settings.height}`
          : "";

      cameraSetupStatus.textContent =
        `準備完了${sizeText}。両手とそろばん全体が白い枠に入ればOKです。`;

      cameraBeginBtn.disabled =
        false;
    } catch (error) {
      cameraSetupStatus.textContent =
        `カメラを開始できませんでした。${error?.name || ""} 『撮影なしで開始』は利用できます。`;
    }
  }

  function videoElapsedMs() {
    if (
      sessionVideoStartedAtPerf ===
      null
    ) {
      return null;
    }

    return Math.max(
      0,
      performance.now() -
        sessionVideoStartedAtPerf
    );
  }

  function startChallengeVideoRecording(
    mode
  ) {
    if (
      !analysisCameraStream ||
      typeof MediaRecorder ===
        "undefined"
    ) {
      return false;
    }

    resetSessionVideoData();

    sessionVideoMode =
      mode;

    sessionVideoStartedAtIso =
      new Date().toISOString();

    sessionVideoStartedAtPerf =
      performance.now();

    sessionVideoActive = true;
    sessionVideoDiscardOnStop =
      false;

    const mimeType =
      chooseSessionVideoMimeType();

    const options = {
      videoBitsPerSecond:
        1200000
    };

    if (mimeType) {
      options.mimeType =
        mimeType;
    }

    try {
      sessionMediaRecorder =
        new MediaRecorder(
          analysisCameraStream,
          options
        );
    } catch (error) {
      sessionMediaRecorder =
        new MediaRecorder(
          analysisCameraStream
        );
    }

    sessionVideoMimeType =
      sessionMediaRecorder.mimeType ||
      mimeType ||
      "video/webm";

    sessionVideoChunks = [];

    sessionMediaRecorder.ondataavailable =
      (event) => {
        if (
          event.data &&
          event.data.size > 0
        ) {
          sessionVideoChunks.push(
            event.data
          );
        }
      };

    sessionMediaRecorder.onerror =
      () => {
        sessionVideoActive =
          false;

        recordingBadge.classList.remove(
          "show"
        );
      };

    sessionMediaRecorder.onstop =
      () => {
        sessionVideoActive =
          false;

        recordingBadge.classList.remove(
          "show"
        );

        const discard =
          sessionVideoDiscardOnStop;

        const chunks =
          sessionVideoChunks.slice();

        sessionVideoChunks = [];

        releaseAnalysisCameraStream();

        if (
          discard ||
          chunks.length === 0
        ) {
          if (discard) {
            revokeSessionVideoUrl();
          }

          sessionVideoDiscardOnStop =
            false;
          return;
        }

        const blob =
          new Blob(
            chunks,
            {
              type:
                sessionVideoMimeType ||
                chunks[0]?.type ||
                "video/webm"
            }
          );

        if (!blob.size) {
          return;
        }

        revokeSessionVideoUrl();

        sessionVideoBlob =
          blob;

        sessionVideoUrl =
          URL.createObjectURL(
            blob
          );

        videoReviewStatus.textContent =
          "録画が完了しました。学習ログは端末内に自動保存されています。";

        renderPostSessionCoaching();
      };

    try {
      sessionMediaRecorder.start(
        1000
      );
    } catch (error) {
      sessionVideoActive =
        false;
      releaseAnalysisCameraStream();
      return false;
    }

    recordingBadge.classList.add(
      "show"
    );

    return true;
  }

  function stopChallengeVideoRecording(
    discard = false
  ) {
    sessionVideoDiscardOnStop =
      Boolean(discard);

    if (
      sessionVideoStartedAtPerf !==
      null
    ) {
      sessionVideoDurationMs =
        videoElapsedMs() || 0;
    }

    sessionVideoActive = false;

    recordingBadge.classList.remove(
      "show"
    );

    if (!sessionMediaRecorder) {
      releaseAnalysisCameraStream();
      return;
    }

    try {
      if (
        sessionMediaRecorder.state ===
        "recording"
      ) {
        try {
          sessionMediaRecorder.requestData();
        } catch (error) {}

        sessionMediaRecorder.stop();
      } else {
        releaseAnalysisCameraStream();
      }
    } catch (error) {
      releaseAnalysisCameraStream();
    }
  }

  function discardChallengeVideoSession() {
    closeCameraSetup(true);
    pendingVideoMode = null;

    if (
      sessionMediaRecorder &&
      sessionMediaRecorder.state ===
        "recording"
    ) {
      stopChallengeVideoRecording(
        true
      );
    } else {
      releaseAnalysisCameraStream();
    }

    revokeSessionVideoUrl();

    sessionQuestionMarks = [];
    sessionVideoMode = null;
    sessionVideoStartedAtPerf = null;
    sessionVideoStartedAtIso = null;
    sessionVideoDurationMs = 0;

    videoReviewPanel.classList.add(
      "hidden"
    );
  }

  function requestStartMode(
    mode,
    sourceButton = null
  ) {
    if (
      !analysisVideoEnabled
    ) {
      resetSessionVideoData();
      startMode(mode);
      return;
    }

    openCameraSetup(
      mode,
      sourceButton
    );
  }

  function beginRecordedChallenge() {
    const mode =
      pendingVideoMode;

    if (
      !mode ||
      !analysisCameraStream
    ) {
      return;
    }

    const started =
      startChallengeVideoRecording(
        mode
      );

    if (!started) {
      cameraSetupStatus.textContent =
        "録画を開始できませんでした。『撮影なしで開始』を選んでください。";
      return;
    }

    pendingVideoMode = null;

    closeCameraSetup(
      false
    );

    startMode(
      mode
    );
  }

  function beginChallengeWithoutVideo() {
    const mode =
      pendingVideoMode;

    if (!mode) {
      closeCameraSetup(true);
      return;
    }

    pendingVideoMode = null;

    closeCameraSetup(
      true
    );

    resetSessionVideoData();

    startMode(
      mode
    );
  }

  function getVideoQuestionMark(
    questionIndex
  ) {
    return (
      sessionQuestionMarks[
        questionIndex
      ] ||
      null
    );
  }

  function recordVideoQuestionStart(
    questionIndex,
    q
  ) {
    if (
      !sessionVideoActive ||
      !q
    ) {
      return;
    }

    let mark =
      getVideoQuestionMark(
        questionIndex
      );

    if (!mark) {
      mark = {
        index:
          questionIndex,
        mode:
          currentMode,
        formula:
          `${q.a} ${q.op} ${q.b}`,
        expected:
          q.answer,
        isWeakness:
          Boolean(
            q.isWeakness
          ),
        startMs: null,
        answerMs: null,
        endMs: null,
        user: null,
        ok: null,
        hintUsed: false,
        hintCount: 0
      };

      sessionQuestionMarks[
        questionIndex
      ] = mark;
    }

    if (
      mark.startMs === null
    ) {
      mark.startMs =
        videoElapsedMs();
    }
  }

  function recordVideoQuestionAnswer(
    questionIndex,
    user,
    ok
  ) {
    if (
      sessionVideoStartedAtPerf ===
      null
    ) {
      return;
    }

    const q =
      questions[
        questionIndex
      ];

    if (!q) {
      return;
    }

    recordVideoQuestionStart(
      questionIndex,
      q
    );

    const mark =
      getVideoQuestionMark(
        questionIndex
      );

    if (!mark) {
      return;
    }

    const now =
      videoElapsedMs();

    mark.answerMs =
      now;

    mark.endMs =
      now;

    mark.user =
      user;

    mark.ok =
      Boolean(ok);
  }

  function recordVideoHintUse(
    questionIndex
  ) {
    if (
      sessionVideoStartedAtPerf ===
      null
    ) {
      return;
    }

    const q =
      questions[
        questionIndex
      ];

    if (!q) {
      return;
    }

    recordVideoQuestionStart(
      questionIndex,
      q
    );

    const mark =
      getVideoQuestionMark(
        questionIndex
      );

    if (!mark) {
      return;
    }

    mark.hintUsed = true;
    mark.hintCount += 1;
  }

  function finalizeVideoQuestionMarks() {
    if (
      sessionVideoStartedAtPerf ===
      null
    ) {
      return;
    }

    const now =
      videoElapsedMs();

    sessionVideoDurationMs =
      now || 0;

    sessionQuestionMarks.forEach(
      (mark) => {
        if (
          mark &&
          mark.endMs === null
        ) {
          mark.endMs =
            now;
        }
      }
    );
  }

  function formatVideoSeconds(
    ms
  ) {
    if (
      ms === null ||
      ms === undefined ||
      !Number.isFinite(
        Number(ms)
      )
    ) {
      return "-";
    }

    return (
      Number(ms) / 1000
    ).toFixed(1) + "秒";
  }

  function medianNumber(
    values
  ) {
    if (!values.length) {
      return 0;
    }

    const sorted =
      values
        .slice()
        .sort(
          (a, b) =>
            a - b
        );

    const mid =
      Math.floor(
        sorted.length / 2
      );

    if (
      sorted.length % 2
    ) {
      return sorted[mid];
    }

    return (
      sorted[mid - 1] +
      sorted[mid]
    ) / 2;
  }

  function buildCurrentSessionRecord(
    challengeScore,
    timeup
  ) {
    const questionsForLog =
      answers.map((entry, i) => {
        const timing =
          questionTimingLog[i] || {};

        const videoMark =
          sessionQuestionMarks[i] || {};

        const skillId =
          skillForQuestion(
            entry.q
          );

        return {
          index: i,
          a: entry.q.a,
          b: entry.q.b,
          op: entry.q.op,
          answer: entry.q.answer,
          user: entry.user,
          ok: Boolean(entry.ok),
          skillId,
          isWeakness:
            Boolean(
              entry.q.isWeakness
            ),
          responseMs:
            Number(
              timing.responseMs ??
              (
                videoMark.answerMs !== undefined &&
                videoMark.startMs !== undefined &&
                videoMark.answerMs !== null &&
                videoMark.startMs !== null
                  ? videoMark.answerMs -
                    videoMark.startMs
                  : 0
              )
            ) || 0,
          hintUsed:
            (Number(timing.hintCount) || 0) > 0 ||
            Boolean(videoMark.hintUsed),
          hintCount:
            Math.max(
              Number(timing.hintCount) || 0,
              Number(videoMark.hintCount) || 0
            )
        };
      });

    const record = {
      id:
        `${Date.now()}-` +
        Math.random()
          .toString(36)
          .slice(2, 9),
      finishedAt:
        new Date().toISOString(),
      mode:
        currentMode,
      modeTitle:
        activeRemedialSkillId
          ? `補習｜${SKILL_DEFS[activeRemedialSkillId]?.title || ""}`
          : modeInfo[currentMode]?.title,
      remedialSkillId:
        activeRemedialSkillId,
      timeup:
        Boolean(timeup),
      certificationEligible:
        !activeRemedialSkillId &&
        !timeup,
      correctCount:
        Number(score) || 0,
      totalQuestions:
        TOTAL_QUESTIONS,
      maxStreak:
        Number(maxCorrectStreak) || 0,
      finalScore:
        Number(
          challengeScore?.finalScore
        ) || 0,
      videoUsed:
        sessionVideoStartedAtPerf !==
          null,
      videoDurationMs:
        Number(
          sessionVideoDurationMs
        ) || 0,
      questions:
        questionsForLog,
      issues: []
    };

    record.issues =
      analyzeSessionIssues(record);

    return record;
  }

  function medianPositive(values) {
    const clean = values
      .map(Number)
      .filter(
        (value) =>
          Number.isFinite(value) &&
          value > 0
      )
      .sort(
        (a, b) => a - b
      );

    if (!clean.length) {
      return 0;
    }

    const mid =
      Math.floor(clean.length / 2);

    return clean.length % 2
      ? clean[mid]
      : (
          clean[mid - 1] +
          clean[mid]
        ) / 2;
  }

  function analyzeSessionIssues(
    record
  ) {
    const responseTimes =
      (record.questions || [])
        .map((q) => q.responseMs)
        .filter((ms) => ms > 0);

    const medianMs =
      medianPositive(
        responseTimes
      );

    const slowThreshold =
      Math.max(
        7000,
        medianMs > 0
          ? medianMs * 1.45
          : 7000
      );

    const bySkill = {};

    (record.questions || [])
      .forEach((question) => {
        const skillId =
          question.skillId ||
          skillForQuestion(question);

        if (!bySkill[skillId]) {
          bySkill[skillId] = {
            skillId,
            count: 0,
            wrong: 0,
            slow: 0,
            hints: 0,
            score: 0,
            examples: []
          };
        }

        const item =
          bySkill[skillId];

        item.count += 1;

        if (!question.ok) {
          item.wrong += 1;
          item.score += 3;
        }

        if (question.hintUsed) {
          item.hints += 1;
          item.score += 2;
        }

        if (
          question.responseMs >
          slowThreshold
        ) {
          item.slow += 1;
          item.score += 1;
        }

        if (
          !question.ok ||
          question.hintUsed ||
          question.responseMs >
            slowThreshold
        ) {
          if (item.examples.length < 2) {
            item.examples.push(
              `${question.a}${question.op}${question.b}`
            );
          }
        }
      });

    const issues =
      Object.values(bySkill)
        .filter((item) => item.score > 0)
        .sort(
          (a, b) =>
            b.score - a.score
        )
        .slice(0, 3)
        .map((item) => {
          const def =
            SKILL_DEFS[item.skillId];

          const reasons = [];

          if (item.wrong > 0) {
            reasons.push(
              `間違い${item.wrong}問`
            );
          }

          if (item.slow > 0) {
            reasons.push(
              `時間が長い${item.slow}問`
            );
          }

          if (item.hints > 0) {
            reasons.push(
              `ヒント使用${item.hints}問`
            );
          }

          const examples =
            item.examples.length
              ? `（${item.examples.join("、")}）`
              : "";

          return {
            skillId:
              item.skillId,
            title:
              def?.shortIssue ||
              def?.title ||
              "計算の考え方を復習",
            evidence:
              `${reasons.join("・")}${examples}`,
            explanation:
              def?.explanation ||
              "同じ種類の問題をゆっくり確認してから、補習問題で定着を確認します。"
          };
        });

    if (!issues.length) {
      return [{
        skillId: null,
        title:
          "今回は大きなつまずきなし",
        evidence:
          "正誤・回答時間・ヒント利用のログでは、大きな課題は見つかりませんでした。",
        explanation:
          "同じ考え方を別の数字でも安定して使えるか、次回も確認します。"
      }];
    }

    return issues;
  }

  function saveCompletedSession(
    record
  ) {
    if (
      sessionHistorySaved ||
      !record
    ) {
      return;
    }

    sessionHistory.push(record);
    sessionHistory = sessionHistory
      .slice(-MAX_SESSION_HISTORY);

    saveSessionHistory();

    const beforeRank =
      calculateAppRank();

    updateRankCertificationState(
      record
    );

    const afterRank =
      calculateAppRank();

    let newlyEarned = [];

    if (
      afterRank.currentIndex >= 0
    ) {
      newlyEarned =
        ensureEarnedMedalsThrough(
          afterRank.currentIndex,
          record.finishedAt
        );
    }

    sessionHistorySaved = true;
    updateRankUi();

    if (
      afterRank.currentIndex >
        beforeRank.currentIndex &&
      newlyEarned.length
    ) {
      setTimeout(
        () => {
          showRankCelebration(
            afterRank.name,
            newlyEarned
          );
        },
        900
      );
    }
  }

  function renderPostSessionCoaching(
    record = null
  ) {
    const target =
      record ||
      sessionHistory[
        sessionHistory.length - 1
      ] ||
      null;

    if (!target) {
      videoReviewPanel.classList.add(
        "hidden"
      );
      return;
    }

    videoReviewPanel.classList.remove(
      "hidden"
    );

    updateRankUi();

    coachingIssueList.innerHTML = "";

    (target.issues || [])
      .forEach((issue) => {
        const card =
          document.createElement(
            "div"
          );

        card.className =
          "coaching-issue-card";

        const title =
          document.createElement(
            "div"
          );

        title.className =
          "coaching-issue-title";

        title.textContent =
          `課題：${issue.title}`;

        const evidence =
          document.createElement(
            "div"
          );

        evidence.className =
          "coaching-issue-evidence";

        evidence.textContent =
          issue.evidence || "";

        const explain =
          document.createElement(
            "div"
          );

        explain.className =
          "coaching-issue-explain";

        explain.textContent =
          issue.explanation || "";

        card.appendChild(title);
        card.appendChild(evidence);
        card.appendChild(explain);

        if (
          issue.skillId &&
          SKILL_DEFS[issue.skillId]
        ) {
          const actions =
            document.createElement(
              "div"
            );

          actions.className =
            "coaching-issue-actions";

          const lessonButton =
            document.createElement(
              "button"
            );

          lessonButton.type =
            "button";

          lessonButton.textContent =
            "解説を見る";

          lessonButton.addEventListener(
            "click",
            () => {
              openSkillLesson(
                issue.skillId
              );
            }
          );

          const practiceButton =
            document.createElement(
              "button"
            );

          practiceButton.type =
            "button";

          practiceButton.className =
            "primary";

          practiceButton.textContent =
            "補習問題をやる";

          practiceButton.addEventListener(
            "click",
            () => {
              startRemedialPractice(
                issue.skillId
              );
            }
          );

          actions.appendChild(
            lessonButton
          );

          actions.appendChild(
            practiceButton
          );

          card.appendChild(
            actions
          );
        }

        coachingIssueList.appendChild(
          card
        );
      });

    videoReviewStatus.textContent =
      target.videoUsed
        ? "学習ログは端末内へ自動保存済みです。録画動画は必要な場合だけ保存できます。"
        : "学習ログは端末内へ自動保存済みです。";

    $("#downloadSessionVideoBtn").disabled =
      !sessionVideoUrl;

    $("#downloadSessionLogBtn").disabled =
      false;
  }

  function renderVideoReviewPanel() {
    renderPostSessionCoaching();
  }

  function downloadSessionVideo() {
    if (
      !sessionVideoUrl ||
      !sessionVideoBlob
    ) {
      return;
    }

    const extension =
      sessionVideoMimeType
        .includes("mp4")
        ? "mp4"
        : "webm";

    const link =
      document.createElement(
        "a"
      );

    link.href =
      sessionVideoUrl;

    link.download =
      `soroban_${sessionVideoMode || "challenge"}_${Date.now()}.${extension}`;

    document.body.appendChild(
      link
    );

    link.click();
    link.remove();
  }

  function downloadSessionLog() {
    const payload =
      sessionHistory[
        sessionHistory.length - 1
      ] || {
        appVersion:
          APP_VERSION,
        mode:
          currentMode,
        questions: []
      };

    const blob =
      new Blob(
        [
          JSON.stringify(
            payload,
            null,
            2
          )
        ],
        {
          type:
            "application/json"
        }
      );

    const url =
      URL.createObjectURL(
        blob
      );

    const link =
      document.createElement(
        "a"
      );

    link.href = url;
    link.download =
      `soroban_analysis_${payload.mode || "challenge"}_${Date.now()}.json`;

    document.body.appendChild(
      link
    );

    link.click();
    link.remove();

    setTimeout(
      () => {
        URL.revokeObjectURL(url);
      },
      1000
    );
  }


  function toggleTimeLimit() {
    timeLimitEnabled =
      !timeLimitEnabled;

    saveTimeLimitSetting();
    updateTimeLimitUi();
  }

  function startMode(
    mode,
    options = {}
  ) {
    scoreAnimationToken += 1;

    // 「はじめる」のクリック中にWebAudioを先に起動。
    // 以後、マイク開始時も同じBGMノードを使い続ける。
    bgmEngine.ensureContext();
    bgmEngine.ensureRunning();

    currentMode = mode;
    activeRemedialSkillId =
      options.remedialSkillId ||
      null;

    questions = [];
    questionTimingLog = [];
    sessionHistorySaved = false;

    if (
      !sessionVideoActive
    ) {
      sessionQuestionMarks = [];
      sessionVideoMode = null;
      sessionVideoStartedAtPerf = null;
      sessionVideoStartedAtIso = null;
      sessionVideoDurationMs = 0;
    }

    videoReviewPanel.classList.add(
      "hidden"
    );

    index = 0;
    gowasanSpokenForIndex = -1;
    score = 0;
    answers = [];
    seconds = 300;
    locked = false;
    correctStreak = 0;
    maxCorrectStreak = 0;
    currentBgm = null;
    currentBgmGroupKey = null;

    sessionWeakAsked = 0;
    sessionWeakCorrect = 0;
    promotedMode = null;
    sessionRankingSaved = false;
    sessionChallengeRecorded = false;
    closeRankingModal();

    $("#retryBtn").textContent = "同じモードをもう一度";

    homeView.classList.add("hidden");
    learningView.classList.remove("show");
    anzanView.classList.remove("show");
    settingsView.classList.remove("show");
    menuView.classList.add("hidden");
    workspace.classList.add("show");
    questionCard.classList.remove(
      "hidden",
      "voice-waiting"
    );
    results.classList.remove("show");

    $("#statusTitle").textContent =
      activeRemedialSkillId
        ? `補習｜${SKILL_DEFS[activeRemedialSkillId].title}`
        : modeInfo[mode].title;

    $("#streak").textContent = "連続正解 0";

    hintBtn.disabled =
      sessionVideoActive;

    hintBtn.classList.toggle(
      "recording-disabled",
      sessionVideoActive
    );

    hintBtn.textContent =
      sessionVideoActive
        ? "撮影中：終了後に復習"
        : "珠ヒントを見る（手動＋音声）";

    voiceAnswerEnabled =
      preferredVoiceAnswerEnabled;

    updateVoiceUi(
      voiceAnswerEnabled
        ? "音声回答：開始します…"
        : "音声回答：OFF"
    );

    feedback.textContent = "";
    feedback.className = "feedback";

    updateStreakFrame(0);

    stopTimer();
    updateTimeLimitUi();
    updateTimer();

    if (
      timeLimitEnabled &&
      !activeRemedialSkillId
    ) {
      timerHandle = setInterval(() => {
        seconds -= 1;
        updateTimer();

        if (seconds <= 0) {
          finish(true);
        }
      }, 1000);
    }

    if (activeRemedialSkillId) {
      timerEl.textContent = "補習";
      statusSub.textContent =
        "補習問題12問・時間制限なし";
    }

    playBgmForStreak(0, true);
    renderQuestion();
  }

  function renderQuestion(voiceReady = false) {
    hideSorobanHint();

    // 音声モードでは、
    // 「ゴワサン！」→マイクonstart の前に問題を作らない。
    if (
      voiceAnswerEnabled &&
      !voiceReady
    ) {
      hideQuestionForVoiceCallout();

      // 前の問題が一瞬見えないように表示内容も消しておく。
      equation.textContent = "";
      answerInput.value = "";
      feedback.textContent = "";
      feedback.className = "feedback";

      $("#qCount").textContent =
        `${index + 1} / ${TOTAL_QUESTIONS}`;
      $("#score").textContent =
        `正解 ${score}`;

      $("#weakBadge").classList.remove(
        "show"
      );

      bar.style.width =
        `${(index / TOTAL_QUESTIONS) * 100}%`;

      // 前問の判定後は locked=true なので、
      // 次のゴワサン→マイク開始へ進めるようここで解除する。
      locked = false;

      if (!voicePauseForFeedback) {
        scheduleVoiceRestart(350);
      }

      return;
    }

    // 非音声モード、またはマイクonstart確認後だけ
    // ここで問題を生成する。
    if (!questions[index]) {
      questions[index] =
        makeQuestionForIndex(
          index,
          currentMode
        );
    }

    const q = questions[index];

    if (!isValidQuestion(q)) {
      throw new Error(
        `表示禁止問題を検出: ${q.a} ${q.op} ${q.b}`
      );
    }

    $("#qCount").textContent =
      `${index + 1} / ${TOTAL_QUESTIONS}`;

    $("#score").textContent =
      `正解 ${score}`;

    $("#weakBadge").classList.toggle(
      "show",
      Boolean(q.isWeakness)
    );

    bar.style.width =
      `${(index / TOTAL_QUESTIONS) * 100}%`;

    equation.textContent =
      `${q.a} ${q.op} ${q.b} ＝ ?`;

    markQuestionDisplayed(
      index
    );

    recordVideoQuestionStart(
      index,
      q
    );

    answerInput.value = "";
    feedback.textContent = "";
    feedback.className = "feedback";
    locked = false;

    // ここへ voiceReady=true で来た時点では、
    // マイクはすでに聞き取り状態。
    revealQuestionAfterVoiceCallout();

    // 10問連続正解以上では、
    // 問題が実際に表示された瞬間だけ発光。
    flashQuestionFrameIfNeeded();
  }


  function submitAnswer() {
    if (locked || index >= TOTAL_QUESTIONS) return;

    const raw = String(answerInput.value || "").trim();

    if (raw === "") {
      feedback.textContent = "答えを入力してください";
      feedback.className = "feedback bad";
      return;
    }

    const user = Number(raw);
    if (!Number.isFinite(user)) return;

    locked = true;

    const q = questions[index];
    const ok = user === q.answer;

    markQuestionAnswered(
      index
    );

    recordVideoQuestionAnswer(
      index,
      user,
      ok
    );

    recordAnswer(currentMode, q, ok);

    if (q.isWeakness && ok) {
      sessionWeakCorrect += 1;
    }

    if (ok) {
      score += 1;
      correctStreak += 1;
      maxCorrectStreak = Math.max(
        maxCorrectStreak,
        correctStreak
      );
    } else {
      correctStreak = 0;
    }

    // 正解時：「正解！○問連続正解中！」
    // 不正解時：「残念！」
    speakAnswerFeedback(ok, correctStreak);

    $("#score").textContent = `正解 ${score}`;
    $("#streak").textContent = `連続正解 ${correctStreak}`;

    updateStreakFrame(correctStreak);
    playBgmForStreak(correctStreak);

    answers.push({ q, user, ok });

    feedback.textContent = ok ? "お見事！" : `残念！ 答えは ${q.answer}`;
    feedback.className = `feedback ${ok ? "good" : "bad"}`;

    setTimeout(() => {
      index += 1;

      if (index >= TOTAL_QUESTIONS) {
        finish(false);
      } else {
        renderQuestion();
      }
    }, 850);
  }

  function updateTimer() {
    if (!timeLimitEnabled) {
      timerEl.textContent =
        "制限なし";

      timerEl.classList.remove(
        "warn"
      );

      return;
    }

    const safe = Math.max(0, seconds);
    const min = Math.floor(safe / 60);
    const sec = safe % 60;

    timerEl.textContent =
      `${String(min).padStart(2, "0")}:` +
      `${String(sec).padStart(2, "0")}`;

    timerEl.classList.toggle(
      "warn",
      seconds <= 60
    );
  }

  function stopTimer() {
    if (timerHandle) {
      clearInterval(timerHandle);
      timerHandle = null;
    }
  }

  function calculateChallengeScore() {
    const wrongCount = answers.filter(
      (entry) =>
        entry.user !== null &&
        !entry.ok
    ).length;

    // 得点計算では残り時間を秒まで反映する。
    // 例：6分48秒 = 408秒 = 6.8分として計算。
    const remainingSeconds =
      Math.max(0, seconds);

    const remainingMinutes =
      remainingSeconds / 60;

    const rawScore =
      score *
      maxCorrectStreak *
      remainingMinutes /
      (wrongCount + 1);

    return {
      correctCount: score,
      maxStreak: maxCorrectStreak,
      remainingSeconds,
      remainingMinutes,
      wrongCount,
      rawScore,
      finalScore: Math.max(
        0,
        Math.round(rawScore)
      )
    };
  }

  function playBabaanSound() {
    try {
      const AudioContextClass =
        window.AudioContext ||
        window.webkitAudioContext;

      if (!AudioContextClass) {
        return;
      }

      const ctx = new AudioContextClass();
      const now = ctx.currentTime;

      const master = ctx.createGain();
      master.gain.setValueAtTime(0.0001, now);
      master.gain.exponentialRampToValueAtTime(
        0.72,
        now + 0.025
      );
      master.gain.exponentialRampToValueAtTime(
        0.0001,
        now + 1.05
      );
      master.connect(ctx.destination);

      const notes = [
        { freq: 196.00, start: 0.00, dur: .78 },
        { freq: 261.63, start: 0.07, dur: .82 },
        { freq: 329.63, start: 0.14, dur: .90 },
        { freq: 523.25, start: 0.25, dur: .74 }
      ];

      notes.forEach((note, index) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type =
          index === notes.length - 1
            ? "triangle"
            : "sawtooth";

        osc.frequency.setValueAtTime(
          note.freq,
          now + note.start
        );

        gain.gain.setValueAtTime(
          0.0001,
          now + note.start
        );
        gain.gain.exponentialRampToValueAtTime(
          index === notes.length - 1 ? .28 : .16,
          now + note.start + .025
        );
        gain.gain.exponentialRampToValueAtTime(
          0.0001,
          now + note.start + note.dur
        );

        osc.connect(gain);
        gain.connect(master);

        osc.start(now + note.start);
        osc.stop(now + note.start + note.dur + .04);
      });

      const drum = ctx.createOscillator();
      const drumGain = ctx.createGain();

      drum.type = "sine";
      drum.frequency.setValueAtTime(120, now);
      drum.frequency.exponentialRampToValueAtTime(
        46,
        now + .42
      );

      drumGain.gain.setValueAtTime(.36, now);
      drumGain.gain.exponentialRampToValueAtTime(
        .0001,
        now + .48
      );

      drum.connect(drumGain);
      drumGain.connect(master);

      drum.start(now);
      drum.stop(now + .5);

      setTimeout(() => {
        ctx.close().catch(() => {});
      }, 1400);
    } catch (error) {
      // 効果音に失敗しても得点表示は続行する。
    }
  }

  function launchConfetti() {
    scoreStage
      .querySelectorAll(".confetti-piece")
      .forEach((node) => node.remove());

    const hues = [
      8, 32, 52, 105, 175, 210, 260, 315
    ];

    for (let i = 0; i < 42; i += 1) {
      const piece = document.createElement("span");
      piece.className = "confetti-piece";

      piece.style.left =
        `${Math.random() * 100}%`;

      piece.style.background =
        `hsl(${hues[i % hues.length]} 90% 58%)`;

      piece.style.setProperty(
        "--drift",
        `${-90 + Math.random() * 180}px`
      );

      piece.style.setProperty(
        "--spin",
        `${360 + Math.floor(Math.random() * 720)}deg`
      );

      piece.style.animationDelay =
        `${Math.random() * .16}s`;

      scoreStage.appendChild(piece);
    }

    setTimeout(() => {
      scoreStage
        .querySelectorAll(".confetti-piece")
        .forEach((node) => node.remove());
    }, 2200);
  }

  function speakFinalScore(scoreInfo) {
    if (
      !("speechSynthesis" in window) ||
      typeof SpeechSynthesisUtterance === "undefined"
    ) {
      return;
    }

    window.speechSynthesis.cancel();

    const points =
      scoreInfo.finalScore;

    const challengeCount =
      Math.max(
        0,
        Number(
          scoreInfo.challengeCount
        ) || 0
      );

    const challengeMessage =
      (
        challengeCount > 0 &&
        challengeCount % 5 === 0
      )
        ? `今回で${modeVoiceName(scoreInfo.mode)}モードは${challengeCount}回目のチャレンジでした。がんばってますね。`
        : null;

    let openingMessage = null;

    if (
      scoreInfo.correctCount === TOTAL_QUESTIONS &&
      scoreInfo.wrongCount === 0
    ) {
      openingMessage =
        "お見事！パーフェクト！";
    } else if (
      scoreInfo.correctCount === TOTAL_QUESTIONS - 1 &&
      scoreInfo.wrongCount === 1
    ) {
      openingMessage =
        "惜しい！もう少しでパーフェクト！";
    }

    const makeUtterance = (
      message,
      rate = 0.96,
      pitch = 1.03
    ) => {
      const utterance =
        new SpeechSynthesisUtterance(
          message
        );

      utterance.lang = "ja-JP";
      utterance.rate = rate;
      utterance.pitch = pitch;
      utterance.volume = 1.0;

      const voice =
        getJapaneseVoice();

      if (voice) {
        utterance.voice = voice;
      }

      return utterance;
    };

    const speakChallengeMessage = () => {
      if (!challengeMessage) {
        return;
      }

      const challengeUtterance =
        makeUtterance(
          challengeMessage,
          0.95,
          1.02
        );

      window.speechSynthesis.speak(
        challengeUtterance
      );
    };

    const speakPoints = () => {
      const scoreUtterance =
        makeUtterance(
          `得点は、${points}点！`,
          0.98,
          1.04
        );

      if (challengeMessage) {
        let continued = false;

        const continueToChallenge = () => {
          if (continued) {
            return;
          }

          continued = true;

          setTimeout(() => {
            speakChallengeMessage();
          }, 260);
        };

        scoreUtterance.onend =
          continueToChallenge;

        scoreUtterance.onerror =
          continueToChallenge;
      }

      window.speechSynthesis.speak(
        scoreUtterance
      );
    };

    if (openingMessage) {
      const openingUtterance =
        makeUtterance(
          openingMessage,
          0.96,
          1.03
        );

      let continued = false;

      const continueToScore = () => {
        if (continued) {
          return;
        }

        continued = true;

        setTimeout(() => {
          speakPoints();
        }, 220);
      };

      openingUtterance.onend =
        continueToScore;

      openingUtterance.onerror =
        continueToScore;

      window.speechSynthesis.speak(
        openingUtterance
      );

      return;
    }

    speakPoints();
  }

  function speakUntimedFinish(scoreInfo) {
    if (
      !("speechSynthesis" in window) ||
      typeof SpeechSynthesisUtterance === "undefined"
    ) {
      return;
    }

    const messages = [];

    if (
      scoreInfo.correctCount === TOTAL_QUESTIONS &&
      scoreInfo.wrongCount === 0
    ) {
      messages.push(
        "お見事！パーフェクト！"
      );
    } else if (
      scoreInfo.correctCount === TOTAL_QUESTIONS - 1 &&
      scoreInfo.wrongCount === 1
    ) {
      messages.push(
        "惜しい！もう少しでパーフェクト！"
      );
    }

    if (
      scoreInfo.challengeCount > 0 &&
      scoreInfo.challengeCount % 5 === 0
    ) {
      messages.push(
        `今回で${modeVoiceName(scoreInfo.mode)}モードは${scoreInfo.challengeCount}回目のチャレンジでした。がんばってますね。`
      );
    }

    if (messages.length === 0) {
      return;
    }

    window.speechSynthesis.cancel();

    const speakAt = (index) => {
      if (index >= messages.length) {
        return;
      }

      const utterance =
        new SpeechSynthesisUtterance(
          messages[index]
        );

      utterance.lang = "ja-JP";
      utterance.rate = 0.96;
      utterance.pitch = 1.03;
      utterance.volume = 1.0;

      const voice =
        getJapaneseVoice();

      if (voice) {
        utterance.voice = voice;
      }

      utterance.onend = () => {
        setTimeout(() => {
          speakAt(index + 1);
        }, 220);
      };

      utterance.onerror =
        utterance.onend;

      window.speechSynthesis.speak(
        utterance
      );
    };

    speakAt(0);
  }

  function runScoreRoulette(scoreInfo) {
    scoreAnimationToken += 1;
    const token = scoreAnimationToken;

    const finalPoints = scoreInfo.finalScore;

    scoreStage.classList.remove(
      "babaan",
      "score-rainbow"
    );

    if (finalPoints > 1000) {
      scoreStage.classList.add(
        "score-rainbow"
      );
    }
    resultScore.classList.remove("reveal");
    resultScore.classList.add("roulette");

    const remainMin =
      Math.floor(
        scoreInfo.remainingSeconds / 60
      );

    const remainSec =
      scoreInfo.remainingSeconds % 60;

    scoreFormula.textContent =
      `${scoreInfo.correctCount}正解 × ` +
      `最高${scoreInfo.maxStreak}問連続 × ` +
      `残り${remainMin}分${remainSec}秒 ÷ ` +
      `（${scoreInfo.wrongCount}回ミス＋1）`;

    let tick = 0;

    const rouletteTimer = setInterval(() => {
      if (token !== scoreAnimationToken) {
        clearInterval(rouletteTimer);
        return;
      }

      const spread = Math.max(
        finalPoints * 1.9,
        180
      );

      const randomValue = Math.max(
        0,
        Math.floor(Math.random() * spread)
      );

      resultScore.textContent =
        `${randomValue.toLocaleString()} 点`;

      tick += 1;
    }, 72);

    setTimeout(() => {
      if (token !== scoreAnimationToken) {
        clearInterval(rouletteTimer);
        return;
      }

      clearInterval(rouletteTimer);

      resultScore.classList.remove("roulette");
      resultScore.textContent =
        `${finalPoints.toLocaleString()} 点`;

      // 再アニメーションのためreflow
      void resultScore.offsetWidth;

      resultScore.classList.add("reveal");
      scoreStage.classList.add("babaan");

      playBabaanSound();
      launchConfetti();

      setTimeout(() => {
        if (token === scoreAnimationToken) {
          speakFinalScore(scoreInfo);
        }
      }, 380);

      setTimeout(() => {
        scoreStage.classList.remove("babaan", "score-rainbow");
      }, 900);
    }, 2700);
  }

  function finish(timeup) {
    finalizeVideoQuestionMarks();

    if (
      sessionVideoStartedAtPerf !==
      null
    ) {
      stopChallengeVideoRecording(
        false
      );
    }

    stopTimer();
    stopBgm();
    hideSorobanHint();
    stopVoiceRecognition();

    if (
      timeup &&
      "speechSynthesis" in window
    ) {
      window.speechSynthesis.cancel();
    }

    locked = true;

    if (timeup) {
      for (
        let i = index;
        i < TOTAL_QUESTIONS;
        i += 1
      ) {
        if (!questions[i]) {
          questions[i] =
            makeQuestionForIndex(
              i,
              currentMode
            );
        }

        answers.push({
          q: questions[i],
          user: null,
          ok: false
        });
      }
    }

    questionCard.classList.remove(
      "voice-waiting"
    );

    questionCard.classList.add(
      "hidden"
    );

    results.classList.add("show");

    const challengeScore =
      calculateChallengeScore();

    if (activeRemedialSkillId) {
      challengeScore.challengeCount =
        getChallengeCount(
          currentMode
        );

      challengeScore.mode =
        currentMode;
    } else if (!sessionChallengeRecorded) {
      challengeScore.challengeCount =
        incrementChallengeCount(
          currentMode
        );

      challengeScore.mode =
        currentMode;

      sessionChallengeRecorded = true;
    } else {
      challengeScore.challengeCount =
        getChallengeCount(
          currentMode
        );

      challengeScore.mode =
        currentMode;
    }

    const timedResult =
      timeLimitEnabled &&
      !activeRemedialSkillId;

    scoreStage.classList.toggle(
      "hidden",
      !timedResult
    );

    resultRankingPanel.classList.toggle(
      "hidden",
      !timedResult
    );

    if (timedResult) {
      resultScore.textContent = "---";

      resultScore.classList.remove(
        "roulette",
        "reveal"
      );

      scoreStage.classList.remove(
        "babaan"
      );

      if (!sessionRankingSaved) {
        addScoreRanking(
          currentMode,
          challengeScore,
          timeup
        );

        sessionRankingSaved = true;
      }

      renderResultRanking(
        currentMode
      );
    }

    const weakSummary =
      sessionWeakAsked > 0
        ? `苦手克服 ${sessionWeakCorrect}/${sessionWeakAsked}問。`
        : "今回は苦手克服問題なし。";

    const canPromote =
      !activeRemedialSkillId &&
      !timeup &&
      Boolean(
        NEXT_MODE[currentMode]
      ) &&
      shouldLevelUp(
        currentMode,
        score
      );

    promotedMode =
      canPromote
        ? NEXT_MODE[currentMode]
        : null;

    let resultMessage;

    if (activeRemedialSkillId) {
      resultMessage =
        `補習12問終了。正解は${score}問です。`;
    } else if (timeup) {
      resultMessage =
        `5分になりました。正解は${score}問です。${weakSummary}`;
    } else if (timeLimitEnabled) {
      resultMessage =
        `12問終了。残り時間は${timerEl.textContent}、正解は${score}問です。${weakSummary}`;
    } else {
      resultMessage =
        `12問終了。正解は${score}問です。${weakSummary}`;
    }

    if (!activeRemedialSkillId) {
      resultMessage +=
        ` 総チャレンジ回数は${challengeScore.challengeCount}回です。`;
    }

    if (promotedMode) {
      resultMessage +=
        ` 苦手を克服できたので、次は「${modeInfo[promotedMode].title}」へレベルアップします。`;

      $("#retryBtn").textContent =
        "レベルアップして次へ";
    } else if (
      !timeup &&
      sessionWeakAsked > 0
    ) {
      resultMessage +=
        " 苦手は次回も優先して出題します。";
    }

    $("#resultNote").textContent =
      resultMessage;

    const review = $("#review");
    review.innerHTML = "";

    answers.forEach(
      (entry, i) => {
        const div =
          document.createElement(
            "div"
          );

        div.className =
          `review-item ${entry.ok ? "correct" : "wrong"}`;

        const weakMark =
          entry.q.isWeakness
            ? "【苦手克服】"
            : "";

        div.textContent =
          `${i + 1}. ${weakMark}${entry.q.a} ${entry.q.op} ${entry.q.b}` +
          ` ＝ ${entry.q.answer}｜` +
          (
            entry.user === null
              ? "未回答"
              : `回答 ${entry.user}`
          );

        review.appendChild(div);
      }
    );

    const sessionRecord =
      buildCurrentSessionRecord(
        challengeScore,
        timeup
      );

    saveCompletedSession(
      sessionRecord
    );

    renderPostSessionCoaching(
      sessionRecord
    );

    if (timedResult) {
      // 5分モードだけ得点を表示・読み上げる。
      setTimeout(() => {
        runScoreRoulette(
          challengeScore
        );
      }, 450);
    } else {
      // 時間制限OFFでは得点は表示・読み上げない。
      setTimeout(() => {
        speakUntimedFinish(
          challengeScore
        );
      }, 450);
    }
  }

  function challengeIsOnTop() {
    return (
      !workspace.classList.contains("show") &&
      !rankingModal.classList.contains("show")
    );
  }

  function pushInAppBackGuard() {
    try {
      window.history.pushState(
        {
          sorobanInApp: true,
          guard: Date.now()
        },
        "",
        window.location.href
      );
    } catch (error) {}
  }

  function installInAppBackGuard() {
    if (inAppBackGuardReady) {
      return;
    }

    inAppBackGuardReady = true;

    // 最初に1つアプリ内履歴を作っておく。
    pushInAppBackGuard();

    window.addEventListener(
      "popstate",
      () => {
        if (handlingBrowserBack) {
          return;
        }

        handlingBrowserBack = true;

        try {
          if (
            cameraSetupModal.classList.contains(
              "show"
            )
          ) {
            pendingVideoMode = null;
            closeCameraSetup(true);
          } else if (
            rankingModal.classList.contains("show")
          ) {
            closeRankingModal();
          } else if (
            workspace.classList.contains("show") ||
            results.classList.contains("show")
          ) {
            // チャレンジ中／結果画面 → チャレンジのモード選択。
            goMenu();
          } else if (
            !learningLessonView.classList.contains("hidden")
          ) {
            // 学習STEP詳細 → STEP一覧。
            showLearningStages(
              learningCurrentCategory
            );
          } else if (
            learningView.classList.contains("show") &&
            !learningStageView.classList.contains("hidden")
          ) {
            // STEP一覧 → 足し算・引き算などの種類選択。
            showLearningHome();
          } else if (
            learningView.classList.contains("show")
          ) {
            // 学習モードの種類選択 → アプリTOP。
            showHome();
          } else if (
            anzanView.classList.contains("show") &&
            (
              !anzanGameView.classList.contains("hidden") ||
              !anzanResultView.classList.contains("hidden")
            )
          ) {
            // 暗算プレイ中／結果 → 暗算レベル選択。
            showAnzanHome();
          } else if (
            anzanView.classList.contains("show")
          ) {
            // 暗算レベル選択 → アプリTOP。
            showHome();
          } else if (
            settingsView.classList.contains("show")
          ) {
            // 設定 → アプリTOP。
            showHome();
          } else if (
            !menuView.classList.contains("hidden")
          ) {
            // チャレンジのモード選択 → アプリTOP。
            showHome();
          } else {
            // アプリTOPでは外へ抜けず、そのまま維持。
            showHome();
          }
        } finally {
          // 次回のBackもアプリ内で処理できるようガードを戻す。
          setTimeout(() => {
            pushInAppBackGuard();
            handlingBrowserBack = false;
          }, 30);
        }
      }
    );
  }

  function goMenu() {
    // Always restore the challenge menu before cleanup.
    forceChallengeMenuView();

    if (
      sessionVideoActive ||
      cameraSetupModal.classList.contains("show")
    ) {
      runUiCleanup(discardChallengeVideoSession);
    }

    scoreAnimationToken += 1;
    runUiCleanup(closeRankingModal);
    runUiCleanup(stopTimer);
    runUiCleanup(stopBgm);
    runUiCleanup(hideSorobanHint);
    runUiCleanup(() => setVoiceAnswerEnabled(false));

    if ("speechSynthesis" in window) {
      runUiCleanup(() => window.speechSynthesis.cancel());
    }

    questionCard.classList.remove("hidden");
    currentMode = null;
    correctStreak = 0;
    runUiCleanup(() => updateStreakFrame(0));
  }

  const rankCelebrationCloseBtn =
    $("#rankCelebrationClose");

  if (rankCelebrationCloseBtn) {
    rankCelebrationCloseBtn.addEventListener(
      "click",
      closeRankCelebration
    );
  }

  if (rankCelebration) {
    rankCelebration.addEventListener(
      "click",
      (event) => {
        if (
          event.target ===
          rankCelebration
        ) {
          closeRankCelebration();
        }
      }
    );
  }

  $("#learningStageBackBtn").addEventListener(
    "click",
    showLearningHome
  );

  $("#learningLessonBackBtn").addEventListener(
    "click",
    () => {
      showLearningStages(
        learningCurrentCategory
      );
    }
  );

  $("#learningReplayBtn").addEventListener(
    "click",
    () => {
      runLearningAnimation(
        true
      );
    }
  );

  learningPauseBtn.addEventListener(
    "click",
    () => {
      learningAnimationPaused =
        !learningAnimationPaused;

      learningPauseBtn.textContent =
        learningAnimationPaused
          ? "▶ 再開"
          : "Ⅱ 一時停止";

      if (
        learningAnimationPaused
      ) {
        stopLearningNarration();
      } else if (
        learningVoiceEnabled
      ) {
        const lesson =
          currentLearningLesson();

        if (lesson) {
          const displayedIndex =
            Math.max(
              0,
              Math.min(
                lesson.frames.length - 1,
                learningCurrentFrame - 1
              )
            );

          speakLearningFrame(
            lesson,
            displayedIndex
          );
        }
      }
    }
  );

  learningVoiceBtn.addEventListener(
    "click",
    () => {
      setLearningVoiceEnabled(
        !learningVoiceEnabled
      );
    }
  );

  $("#learningAgainBtn").addEventListener(
    "click",
    () => {
      const lesson =
        currentLearningLesson();

      if (lesson) {
        renderLearningQuiz(
          lesson
        );
      }

      runLearningAnimation(
        true
      );
    }
  );

  learningNextBtn.addEventListener(
    "click",
    goNextLearningLesson
  );

  root
    .querySelectorAll(
      ".start-anzan"
    )
    .forEach(
      (button) => {
        button.addEventListener(
          "click",
          () => {
            startAnzanMode(
              button.dataset.anzanLevel
            );
          }
        );
      }
    );

  $("#anzanKeypad").addEventListener(
    "pointerup",
    (event) => {
      if (anzanLocked) {
        return;
      }

      const button =
        event.target.closest(
          "button"
        );

      if (!button) {
        return;
      }

      const key =
        button.dataset.anzanKey;

      const action =
        button.dataset.anzanAction;

      if (
        key !== undefined
      ) {
        if (
          anzanAnswerBuffer.length < 8
        ) {
          anzanAnswerBuffer =
            anzanAnswerBuffer === "0"
              ? key
              : anzanAnswerBuffer + key;
        }

        updateAnzanAnswerDisplay();
        return;
      }

      if (
        action === "clear"
      ) {
        anzanAnswerBuffer = "";
      } else if (
        action === "backspace"
      ) {
        anzanAnswerBuffer =
          anzanAnswerBuffer.slice(
            0,
            -1
          );
      }

      updateAnzanAnswerDisplay();
    }
  );

  $("#anzanSubmitBtn").addEventListener(
    "click",
    submitAnzanAnswer
  );

  $("#anzanRetryBtn").addEventListener(
    "click",
    () => {
      startAnzanMode(
        anzanLevel
      );
    }
  );

  $("#anzanMenuBtn").addEventListener(
    "click",
    showAnzanHome
  );

  $("#settingTimeBtn").addEventListener(
    "click",
    () => {
      toggleTimeLimit();
      updateSettingsUi();
    }
  );

  $("#settingVoiceBtn").addEventListener(
    "click",
    () => {
      preferredVoiceAnswerEnabled =
        !preferredVoiceAnswerEnabled;

      saveBooleanSetting(
        VOICE_DEFAULT_SETTING_KEY,
        preferredVoiceAnswerEnabled
      );

      updateSettingsUi();
    }
  );

  $("#settingBgmBtn").addEventListener(
    "click",
    () => {
      bgmOn =
        !bgmOn;

      saveBooleanSetting(
        BGM_SETTING_KEY,
        bgmOn
      );

      updateBgmButton();
      updateSettingsUi();
    }
  );

  $("#settingLearningVoiceBtn").addEventListener(
    "click",
    () => {
      preferredLearningVoiceEnabled =
        !preferredLearningVoiceEnabled;

      learningVoiceEnabled =
        preferredLearningVoiceEnabled;

      saveBooleanSetting(
        LEARNING_VOICE_SETTING_KEY,
        preferredLearningVoiceEnabled
      );

      updateLearningVoiceUi();
      updateSettingsUi();
    }
  );

  $("#settingAnzanSpeedBtn").addEventListener(
    "click",
    cycleAnzanSpeed
  );

  voiceBtn.addEventListener("click", () => {
    preferredVoiceAnswerEnabled =
      !voiceAnswerEnabled;

    saveBooleanSetting(
      VOICE_DEFAULT_SETTING_KEY,
      preferredVoiceAnswerEnabled
    );

    setVoiceAnswerEnabled(
      preferredVoiceAnswerEnabled
    );

    updateSettingsUi();
  });

  hintBtn.addEventListener("click", () => {
    runSorobanHint();
  });

  hintPrevBtn.addEventListener("click", () => {
    moveManualHint(-1);
  });

  hintNextBtn.addEventListener("click", () => {
    moveManualHint(1);
  });

  $("#hintCloseBtn").addEventListener("click", () => {
    hideSorobanHint();
  });

  $("#keypad").addEventListener("pointerup", (event) => {
    const button = event.target.closest("button");
    if (!button || locked) return;

    const key = button.dataset.key;
    const action = button.dataset.action;

    if (key !== undefined) {
      const current = String(answerInput.value || "");

      if (current.length < 10) {
        answerInput.value =
          current === "0" ? key : current + key;
      }
      return;
    }

    if (action === "clear") {
      answerInput.value = "";
      return;
    }

    if (action === "backspace") {
      answerInput.value =
        String(answerInput.value || "").slice(0, -1);
    }
  });

  try {
    rebuildRankCertificationStateFromHistory();
    syncEarnedMedalsFromCurrentRank();
  } catch (error) {
    // 階級・メダルの復元失敗でアプリ全体を停止させない。
    rankCertificationState = {
      bestByMode:
        rankCertificationState?.bestByMode || {},
      medalAchievedAt:
        rankCertificationState?.medalAchievedAt || {}
    };
  }

  setupModeChallengeCountLabels();
  setupModeRankingButtons();

  root.querySelectorAll(".start-mode").forEach((button) => {
    button.addEventListener("click", () => {
      requestStartMode(
        button.dataset.mode,
        button
      );
    });
  });

  $("#rankingModalClose").addEventListener(
    "click",
    closeRankingModal
  );

  rankingModal.addEventListener(
    "click",
    (event) => {
      if (event.target === rankingModal) {
        closeRankingModal();
      }
    }
  );

  analysisVideoToggle.addEventListener(
    "click",
    toggleAnalysisVideo
  );

  cameraBeginBtn.addEventListener(
    "click",
    beginRecordedChallenge
  );

  $("#cameraNoVideoBtn").addEventListener(
    "click",
    beginChallengeWithoutVideo
  );

  $("#cameraCancelBtn").addEventListener(
    "click",
    () => {
      pendingVideoMode = null;
      closeCameraSetup(true);
    }
  );


  $("#downloadSessionVideoBtn").addEventListener(
    "click",
    downloadSessionVideo
  );

  $("#downloadSessionLogBtn").addEventListener(
    "click",
    downloadSessionLog
  );

  $("#answerForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitAnswer();
  });

  timeLimitBtn.addEventListener(
    "click",
    toggleTimeLimit
  );

  bgmBtn.addEventListener("click", toggleBgm);

  $("#retryBtn").addEventListener("click", () => {
    if (promotedMode) {
      requestStartMode(
        promotedMode
      );
      return;
    }

    if (currentMode) {
      requestStartMode(
        currentMode
      );
    }
  });

  timeLimitEnabled =
    loadTimeLimitSetting();

  bgmOn =
    loadBooleanSetting(
      BGM_SETTING_KEY,
      true
    );

  preferredVoiceAnswerEnabled =
    loadBooleanSetting(
      VOICE_DEFAULT_SETTING_KEY,
      false
    );

  preferredLearningVoiceEnabled =
    loadBooleanSetting(
      LEARNING_VOICE_SETTING_KEY,
      false
    );

  learningVoiceEnabled =
    preferredLearningVoiceEnabled;

  anzanSpeed =
    loadAnzanSpeed();

  analysisVideoEnabled =
    loadBooleanSetting(
      ANALYSIS_VIDEO_SETTING_KEY,
      false
    );

  homeView.classList.remove("hidden");
  menuView.classList.add("hidden");
  learningView.classList.remove("show");
  anzanView.classList.remove("show");
  settingsView.classList.remove("show");
  workspace.classList.remove("show");

  updateTimeLimitUi();

  try {
    updateRankUi();
  } catch (error) {
    // メダル表示だけ失敗しても、ゲーム操作は継続する。
  }

  updateAnalysisVideoUi();
  updateLearningVoiceUi();
  updateBgmButton();
  updateSettingsUi();

  try {
    updateRankUi();
  } catch (error) {}

  renderLearningCategories();
  installInAppBackGuard();
  updateBgmButton();
  updateVoiceUi();
})();
</script>
</div>
"""

html = (
    HTML
    .replace("__BGM_URLS__", BGM_JSON)
    .replace("__APP_VERSION__", VERSION_JSON)
)

components.html(
    html,
    height=1600,
    scrolling=True,
)
