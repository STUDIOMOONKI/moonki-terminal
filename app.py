import streamlit as st
import requests
import pandas as pd
import zipfile
import io
import xml.etree.ElementTree as ET
from datetime import datetime
import json
import os
import yfinance as yf
from streamlit_sortables import sort_items
import re

# ==========================================
# 1. 페이지 설정
# ==========================================
st.set_page_config(page_title="Moonki Terminal", layout="wide")

DART_API_KEY = st.secrets["DART_API_KEY"]
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
SAVE_FILE = "moonki_portfolio.json"

if 'current_view' not in st.session_state: st.session_state.current_view = "Matrix"

# ==========================================
# 2. [영구 저장소 엔진]
# ==========================================
def load_portfolio(dart_mapping):
    if os.path.exists(SAVE_FILE):
        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "owned": data.get("owned", []),
                "watched": data.get("watched", []),
                "manual_prices": data.get("manual_prices", {}),
                "financial_cache": data.get("financial_cache", {}),
                "manual_overrides": data.get("manual_overrides", {}),
                "price_cache": data.get("price_cache", {}),
                "company_properties": data.get("company_properties", {}) 
            }
        except: pass
    return {"owned": [], "watched": [], "manual_prices": {}, "financial_cache": {}, "manual_overrides": {}, "price_cache": {}, "company_properties": {}}

def save_portfolio():
    data = {
        "owned": st.session_state.owned_list, 
        "watched": st.session_state.watched_list, 
        "manual_prices": st.session_state.manual_prices, 
        "financial_cache": st.session_state.financial_cache,
        "manual_overrides": st.session_state.manual_overrides,
        "price_cache": st.session_state.price_cache,
        "company_properties": st.session_state.company_properties
    }
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# ==========================================
# 3. [Utils]
# ==========================================
def evaluate_formula(expression):
    try:
        clean_expr = str(expression).replace(",", "").replace(" ", "")
        if not re.match(r'^[0-9\+\-\*\/\.\(\)]+$', clean_expr): return None
        return float(eval(clean_expr))
    except: return None

def safe_float(val):
    if pd.isna(val) or val is None or val == "": return 0.0
    try: return float(val)
    except: return 0.0

def parse_dart_number(val_str):
    if not val_str or str(val_str).strip() == '-': return 0.0
    v_str = str(val_str).replace(',', '').replace(' ', '').replace('　', '').strip()
    if v_str.startswith('(') and v_str.endswith(')'): v_str = '-' + v_str[1:-1]
    elif v_str.startswith('△'): v_str = '-' + v_str[1:]
    try: return float(v_str)
    except: return 0.0

def format_with_commas(val, is_currency=True, region="KR"):
    if val == "-" or pd.isna(val) or val is None: return "-"
    try:
        f_val = float(val)
        if f_val == 0.0: return "0"
        if not is_currency: return f"{f_val:.1f}%"
        if region == "US": return f"{f_val:,.2f}"
        return f"{int(round(f_val)):,}" 
    except:
        return str(val)

@st.cache_data(ttl=86400)
def load_dart_corp_mapping():
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_API_KEY}"
    mapping = {}
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            with z.open('CORPCODE.xml') as f:
                tree = ET.parse(f)
                for node in tree.findall('list'):
                    stock_code = node.find('stock_code').text
                    if stock_code and len(stock_code.strip()) == 6:
                        mapping[stock_code.strip()] = {"corp_code": node.find('corp_code').text.strip(), "name": node.find('corp_name').text.strip()}
    except: pass
    return mapping

dart_mapping = load_dart_corp_mapping()
if 'portfolio_loaded' not in st.session_state:
    saved_data = load_portfolio(dart_mapping)
    st.session_state.owned_list = saved_data["owned"]
    st.session_state.watched_list = saved_data["watched"]
    st.session_state.manual_prices = saved_data["manual_prices"]
    st.session_state.financial_cache = saved_data["financial_cache"]
    st.session_state.manual_overrides = saved_data["manual_overrides"]
    st.session_state.price_cache = saved_data["price_cache"]
    st.session_state.company_properties = saved_data["company_properties"]
    st.session_state.portfolio_loaded = True

if 'sim_fcf' not in st.session_state: st.session_state.sim_fcf = {}
if 'sim_shares' not in st.session_state: st.session_state.sim_shares = {}

for k in ['selected_symbol', 'selected_corp_name', 'selected_corp_code']:
    if k not in st.session_state: st.session_state[k] = ""
if 'selected_region' not in st.session_state: st.session_state.selected_region = "KR"

# ==========================================
# 4. [Core] 매트릭스 백엔드 파서
# ==========================================
def silent_cache_fetch(corp_code, corp_name, symbol, region):
    current_year = datetime.now().year
    years = [str(y) for y in range(2014, current_year)]
    if symbol not in st.session_state.financial_cache: st.session_state.financial_cache[symbol] = {}
    cached_entry = st.session_state.financial_cache[symbol]
    
    ctype = st.session_state.company_properties.get(symbol, "일반")
    fs_list = ["OFS"] if ctype == "지주회사" else ["CFS", "OFS"]
    
    tk_sym = symbol
    if region == "KR":
        if symbol.endswith(".KS") or symbol.endswith(".KQ"):
            tk_sym = symbol
        else:
            try:
                test_df = yf.download(f"{symbol}.KS", period="1d", progress=False)
                tk_sym = f"{symbol}.KQ" if test_df.empty else f"{symbol}.KS"
            except:
                tk_sym = f"{symbol}.KS"
                
    us_fin, us_cf = None, None
    if region == "US":
        try:
            tk = yf.Ticker(tk_sym)
            us_fin, us_cf = tk.financials, tk.cashflow
        except: pass

    for y in years:
        y_idx = y[2:] + "Y"
        if y_idx not in cached_entry: cached_entry[y_idx] = {"shares":0, "rev":0, "gp":0, "op":0, "fcf":0, "div_total":0, "dps":0}
        if cached_entry[y_idx].get("rev", 0) > 0 and cached_entry[y_idx].get("op", 0) != 0: continue
        
        data_block = {"shares":0, "rev":0, "gp":0, "op":0, "fcf":0, "div_total":0, "dps":0}
        
        if ctype == "우선주":
            continue

        if region == "KR":
            try:
                for fs in fs_list:
                    # 💡 DART API Timeout 15초로 연장 (SK, 거대기업 방어)
                    res_f = requests.get(f"https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?crtfc_key={DART_API_KEY}&corp_code={corp_code}&bsns_year={y}&reprt_code=11011&fs_div={fs}", timeout=15).json()
                    if res_f.get('status') == '000':
                        rv, gp, op_val, oc, cx = 0, 0, 0, 0, 0
                        fin_rv_parts = 0 
                        hold_rv_parts = 0 # 💡 지주회사 수익 합산기
                        
                        for it in res_f['list']:
                            sj = it.get('sj_div', '')
                            acc = it.get('account_id', '')
                            nm_clean = "".join(c for c in str(it.get('account_nm','')) if c.isalnum())
                            val = parse_dart_number(it.get('thstrm_amount')) * (1000000.0 if "백만" in it.get('currency','') else 1.0)
                            if val == 0: continue
                            
                            if ctype == "금융":
                                if nm_clean in ['영업수익', '영업수익합계']: rv = max(rv, val)
                                elif any(k in nm_clean for k in ['이자수익', '수수료수익', '배당수익', '보험수익', '신탁업무수익']): fin_rv_parts += val
                                if '순이자손익' in nm_clean or '순이자수익' in nm_clean: gp = max(gp, val)
                                if nm_clean in ['당기순이익', '지배기업주주지분순이익', '연결당기순이익', '지배기업소유주지분순이익', '지배지분순이익']: oc = max(oc, val)
                                if nm_clean in ['영업이익', '영업손실', '영업손익']: op_val = max(op_val, val) 
                                
                            elif ctype == "지주회사":
                                # 💡 지주회사 별도재무제표 맞춤형 파싱
                                if sj in ['IS', 'CIS'] or sj == '':
                                    if acc == 'ifrs-full_Revenue' or nm_clean in ['매출액', '영업수익', '영업수익합계']: rv = max(rv, val)
                                    elif any(k in nm_clean for k in ['배당수익', '상표권수익', '로열티수익', '임대수익', '수수료수익', '지분법수익']):
                                        hold_rv_parts += val
                                    if acc == 'ifrs-full_GrossProfit' or nm_clean in ['매출총이익', '영업총이익']: gp = max(gp, val)
                                    if 'OperatingProfit' in acc or 'OperatingIncome' in acc or nm_clean in ['영업이익', '영업손실', '영업손익']:
                                        if op_val == 0: op_val = val
                                if sj == 'CF' or '현금흐름' in str(it.get('sj_nm','')):
                                    if acc == 'ifrs-full_CashFlowsFromUsedInOperatingActivities' or '영업활동현금흐름' in nm_clean: oc = val
                                    if acc == 'ifrs-full_PurchaseOfPropertyPlantAndEquipment' or ('유형자산' in nm_clean and '취득' in nm_clean): cx += abs(val)
                            else:
                                if sj in ['IS', 'CIS'] or sj == '':
                                    if acc == 'ifrs-full_Revenue' or nm_clean in ['매출액', '영업수익']: rv = max(rv, val)
                                    if acc == 'ifrs-full_GrossProfit' or nm_clean in ['매출총이익', '영업총이익']: gp = max(gp, val)
                                    if 'OperatingProfit' in acc or 'OperatingIncome' in acc or nm_clean in ['영업이익', '영업손실']:
                                        if op_val == 0: op_val = val
                                if sj == 'CF' or '현금흐름' in str(it.get('sj_nm','')):
                                    if acc == 'ifrs-full_CashFlowsFromUsedInOperatingActivities' or '영업활동현금흐름' in nm_clean: oc = val
                                    if acc == 'ifrs-full_PurchaseOfPropertyPlantAndEquipment' or ('유형자산' in nm_clean and '취득' in nm_clean): cx += abs(val)
                        
                        if ctype == "금융" and rv == 0 and fin_rv_parts > 0:
                            rv = fin_rv_parts
                        if ctype == "지주회사" and rv == 0 and hold_rv_parts > 0:
                            rv = hold_rv_parts
                            
                        if rv > 0 or oc != 0:
                            data_block["rev"] = rv
                            data_block["gp"] = gp
                            data_block["op"] = op_val
                            data_block["fcf"] = oc if ctype == "금융" else (oc - cx)
                            break
            except: pass
            
            if (data_block["op"] == 0 or data_block["rev"] == 0) and ctype != "지주회사":
                try:
                    res_f2 = requests.get(f"https://opendart.fss.or.kr/api/fnlttSinglAcnt.json?crtfc_key={DART_API_KEY}&corp_code={corp_code}&bsns_year={y}&reprt_code=11011", timeout=15).json()
                    if res_f2.get('status') == '000':
                        for it in res_f2['list']:
                            nm_clean = "".join(c for c in str(it.get('account_nm','')) if c.isalnum())
                            val = parse_dart_number(it.get('thstrm_amount'))
                            if nm_clean in ['매출액', '영업수익', '영업수익합계'] and data_block["rev"] == 0: data_block["rev"] = val
                            if nm_clean in ['영업이익', '영업손실', '영업손익'] and data_block["op"] == 0: data_block["op"] = val
                            if ctype == "금융" and nm_clean in ['당기순이익', '분기순이익', '반기순이익', '지배기업주주지분순이익'] and data_block["fcf"] == 0: data_block["fcf"] = val
                except: pass
            
            try:
                res_s = requests.get(f"https://opendart.fss.or.kr/api/stockTotqySttus.json?crtfc_key={DART_API_KEY}&corp_code={corp_code}&bsns_year={y}&reprt_code=11011", timeout=15).json()
                if res_s.get('status') == '000':
                    for it in res_s['list']:
                        if '보통주' in it.get('se','') or '합계' in it.get('se',''):
                            v = parse_dart_number(it.get('istc_totqy','0'))
                            if v > 0: data_block["shares"] = v; break 
            except: pass
            try:
                res_d = requests.get(f"https://opendart.fss.or.kr/api/alotMatter.json?crtfc_key={DART_API_KEY}&corp_code={corp_code}&bsns_year={y}&reprt_code=11011", timeout=15).json()
                if res_d.get('status') == '000':
                    for it in res_d['list']:
                        if '현금배당금총액' in it.get('se','').replace(" ",""): data_block["div_total"] = parse_dart_number(it.get('thstrm')) * 1000000.0
                        if '주당현금배당금' in it.get('se','').replace(" ","") and '보통주' in it.get('stock_knd',''): data_block["dps"] = parse_dart_number(it.get('thstrm'))
            except: pass
        else:
            try:
                if us_fin is not None and not us_fin.empty:
                    target_dt = next((col for col in us_fin.columns if str(col.year) == y), None)
                    if target_dt is not None:
                        rev = safe_float(us_fin.loc['Total Revenue', target_dt] if 'Total Revenue' in us_fin.index else 0)
                        gp = safe_float(us_fin.loc['Gross Profit', target_dt] if 'Gross Profit' in us_fin.index else 0)
                        op_val = safe_float(us_fin.loc['Operating Income', target_dt] if 'Operating Income' in us_fin.index else 0)
                        if rev > 0: data_block["rev"], data_block["gp"], data_block["op"] = rev, gp, op_val
                if us_cf is not None and not us_cf.empty:
                    target_dt_cf = next((col for col in us_cf.columns if str(col.year) == y), None)
                    if target_dt_cf is not None:
                        oc = safe_float(us_cf.loc['Operating Cash Flow', target_dt_cf] if 'Operating Cash Flow' in us_cf.index else 0)
                        cx = abs(safe_float(us_cf.loc['Capital Expenditure', target_dt_cf] if 'Capital Expenditure' in us_cf.index else 0))
                        
                        if ctype == "금융":
                            ni = safe_float(us_cf.loc['Net Income', target_dt_cf] if 'Net Income' in us_cf.index else oc)
                            data_block["fcf"] = ni
                        else:
                            if oc != 0: data_block["fcf"] = oc - cx
            except: pass
            
        for k, v in data_block.items():
            if v != 0: cached_entry[y_idx][k] = v

def fetch_matrix_final(corp_code, corp_name, symbol, region):
    current_year = datetime.now().year
    years = [str(y) for y in range(2014, current_year)]
    display_cols = [y[2:] + "Y" for y in years if int(y) >= 2015]
    display_cols.append("Current") 
    currency = "USD" if region == "US" else "KRW"
    
    ctype = st.session_state.company_properties.get(symbol, "일반")
    
    # 💡 [핵심 반영] 어떠한 속성이든 명칭은 절대 변경하지 않고 오리지널 규격 유지
    matrix = pd.DataFrame(index=[
        f"Stock Price ({currency})", "Shares Outstanding", f"Market Cap (Mil {currency})", "Market Cap Growth (%)", "", 
        f"Revenue (Mil {currency})", "Revenue Growth (%)", f"Gross Profit (Mil {currency})", "Gross Margin (%)", 
        f"Operating Income (Mil {currency})", "Operating Margin (%)", "  ", 
        f"Free Cash Flow (Mil {currency})", "FCF Margin (%)", "FCF Growth (%)", "FCF Yield (%)", " ", 
        f"Total Dividends (Mil {currency})", "Total Dividends Growth (%)", f"DPS ({currency})", "FCF Payout Ratio (%)", "Dividend Yield (%)"
    ], columns=display_cols).fillna("-")
    
    matrix.loc[""] = ""; matrix.loc[" "]= ""; matrix.loc["  "] = ""
    silent_cache_fetch(corp_code, corp_name, symbol, region)
    cached_entry = st.session_state.financial_cache[symbol]
    
    auto_p_map = {}
    tk_sym = symbol
    if region == "KR":
        if symbol.endswith(".KS") or symbol.endswith(".KQ"): tk_sym = symbol
        else:
            try:
                test_df = yf.download(f"{symbol}.KS", period="1d", progress=False)
                tk_sym = f"{symbol}.KQ" if test_df.empty else f"{symbol}.KS"
            except: tk_sym = f"{symbol}.KS"
            
    try:
        df_p = yf.download(tk_sym, start="2014-01-01", auto_adjust=False, progress=False)
        if not df_p.empty:
            if isinstance(df_p.columns, pd.MultiIndex): df_p.columns = df_p.columns.get_level_values(0)
            for y in range(2014, current_year):
                y_d = df_p[df_p.index.year == y]
                if not y_d.empty: auto_p_map[f"{str(y)[2:]}Y"] = safe_float(y_d['Close'].iloc[-1])
    except: pass

    UNIT = 1000000.0
    for idx, c in enumerate(display_cols):
        if c == "Current": continue
        rd = cached_entry.get(c, {"shares":0, "rev":0, "gp":0, "op":0, "fcf":0, "div_total":0, "dps":0}).copy()
        overrides = st.session_state.manual_overrides.get(symbol, {}).get(c, {})
        for k, v in overrides.items(): rd[k] = v 

        manual_p = safe_float(st.session_state.manual_prices.get(symbol, {}).get(c.replace("Y","년"), 0))
        final_p = manual_p if manual_p > 0 else safe_float(auto_p_map.get(c, 0))
        mcap_raw = final_p * rd["shares"] if final_p > 0 and rd["shares"] > 0 else 0
        
        if final_p > 0: matrix.at[f"Stock Price ({currency})", c] = format_with_commas(final_p, region=region)
        if rd["shares"] > 0: matrix.at["Shares Outstanding", c] = format_with_commas(rd['shares'])
        if mcap_raw > 0: matrix.at[f"Market Cap (Mil {currency})", c] = format_with_commas(mcap_raw/UNIT)
        
        if ctype != "우선주":
            if rd["rev"] > 0: matrix.at[f"Revenue (Mil {currency})", c] = format_with_commas(rd['rev']/UNIT)
            if rd["gp"] != 0:
                matrix.at[f"Gross Profit (Mil {currency})", c] = format_with_commas(rd['gp']/UNIT)
                if rd["rev"] > 0: matrix.at["Gross Margin (%)", c] = format_with_commas((rd['gp']/rd['rev'])*100, is_currency=False)
            if rd.get("op", 0) != 0:
                matrix.at[f"Operating Income (Mil {currency})", c] = format_with_commas(rd['op']/UNIT)
                if rd["rev"] > 0: matrix.at["Operating Margin (%)", c] = format_with_commas((rd['op']/rd['rev'])*100, is_currency=False)
            if rd["fcf"] != 0: matrix.at[f"Free Cash Flow (Mil {currency})", c] = format_with_commas(rd['fcf']/UNIT)
            if rd["rev"] > 0: matrix.at["FCF Margin (%)", c] = format_with_commas((rd['fcf']/rd['rev'])*100, is_currency=False)
            if rd["fcf"] != 0 and mcap_raw > 0: matrix.at["FCF Yield (%)", c] = format_with_commas((rd['fcf'] / mcap_raw) * 100, is_currency=False)
            if rd["fcf"] > 0 and rd.get("div_total",0) > 0: matrix.at["FCF Payout Ratio (%)", c] = format_with_commas((rd['div_total']/rd['fcf'])*100, is_currency=False)
            
        if rd.get("div_total",0) > 0: matrix.at[f"Total Dividends (Mil {currency})", c] = format_with_commas(rd['div_total']/UNIT)
        if rd.get("dps",0) > 0: matrix.at[f"DPS ({currency})", c] = format_with_commas(rd['dps'], region=region)
        if final_p > 0 and rd.get("dps",0) > 0: matrix.at["Dividend Yield (%)", c] = format_with_commas((rd['dps']/final_p)*100, is_currency=False)

        if idx > 0 and ctype != "우선주":
            prev_c = display_cols[idx-1]
            prev_rd = cached_entry.get(prev_c, {"shares":0, "rev":0, "gp":0, "op":0, "fcf":0}).copy()
            prev_overrides = st.session_state.manual_overrides.get(symbol, {}).get(prev_c, {})
            for k, v in prev_overrides.items(): prev_rd[k] = v
            prev_p = safe_float(st.session_state.manual_prices.get(symbol, {}).get(prev_c.replace("Y","년"), auto_p_map.get(prev_c, 0)))
            prev_mcap_raw = prev_p * prev_rd["shares"] if prev_p > 0 and prev_rd["shares"] > 0 else 0
            if mcap_raw > 0 and prev_mcap_raw > 0: matrix.at["Market Cap Growth (%)", c] = format_with_commas(((mcap_raw - prev_mcap_raw)/prev_mcap_raw)*100, is_currency=False)
            if rd["rev"] > 0 and prev_rd.get("rev",0) > 0: matrix.at["Revenue Growth (%)", c] = format_with_commas(((rd['rev'] - prev_rd['rev'])/prev_rd['rev'])*100, is_currency=False)
            
            curr_fcf, prev_fcf = rd.get("fcf", 0), prev_rd.get("fcf", 0)
            if curr_fcf != 0 and prev_fcf != 0: 
                if curr_fcf > 0 and prev_fcf > 0: matrix.at["FCF Growth (%)", c] = format_with_commas(((curr_fcf - prev_fcf)/prev_fcf)*100, is_currency=False)
                elif curr_fcf > 0 and prev_fcf < 0: matrix.at["FCF Growth (%)", c] = "Turnaround" 
                elif curr_fcf < 0 and prev_fcf > 0: matrix.at["FCF Growth (%)", c] = "Deficit" 
                elif curr_fcf < 0 and prev_fcf < 0: matrix.at["FCF Growth (%)", c] = "N/M" 

    # Current 열
    c_col = "Current"
    cur_p_manual = safe_float(st.session_state.manual_prices.get(symbol, {}).get(c_col, 0))
    cur_price = cur_p_manual
    if cur_price == 0:
        try:
            df_curr = yf.download(tk_sym, period="1d", auto_adjust=False, progress=False)
            if not df_curr.empty:
                cols = df_curr.columns
                if isinstance(cols, pd.MultiIndex): df_curr.columns = cols.get_level_values(0)
                cur_price = safe_float(df_curr['Close'].iloc[-1])
        except: pass

    need_save = False
    if cur_price > 0:
        st.session_state.price_cache[symbol] = cur_price
        need_save = True

    latest_sh, latest_op, latest_fcf = 0, 0, 0
    for y in reversed(years):
        rd_temp = cached_entry.get(f"{str(y)[2:]}Y", {}).copy()
        ov_temp = st.session_state.manual_overrides.get(symbol, {}).get(f"{str(y)[2:]}Y", {})
        for k, v in ov_temp.items(): rd_temp[k] = v
        if latest_sh == 0 and safe_float(rd_temp.get("shares", 0)) > 0: latest_sh = safe_float(rd_temp.get("shares", 0))
        if latest_op == 0 and safe_float(rd_temp.get("op", 0)) != 0: latest_op = safe_float(rd_temp.get("op", 0))
        if latest_fcf == 0 and safe_float(rd_temp.get("fcf", 0)) != 0: latest_fcf = safe_float(rd_temp.get("fcf", 0))

    sim_fcf_val = st.session_state.sim_fcf.get(symbol, None)
    sim_sh_val = st.session_state.sim_shares.get(symbol, None)
    current_fcf_raw = sim_fcf_val * UNIT if sim_fcf_val is not None else latest_fcf
    current_sh_raw = sim_sh_val if sim_sh_val is not None else latest_sh

    if cur_price > 0: matrix.at[f"Stock Price ({currency})", c_col] = format_with_commas(cur_price, region=region)
    if current_sh_raw > 0: matrix.at["Shares Outstanding", c_col] = format_with_commas(current_sh_raw)
    cur_mcap_raw = cur_price * current_sh_raw if cur_price > 0 and current_sh_raw > 0 else 0
    if cur_mcap_raw > 0: matrix.at[f"Market Cap (Mil {currency})", c_col] = format_with_commas(cur_mcap_raw/UNIT)
    
    if ctype != "우선주":
        if latest_op != 0: matrix.at[f"Operating Income (Mil {currency})", c_col] = format_with_commas(latest_op/UNIT)
        if current_fcf_raw != 0 or sim_fcf_val is not None:
            matrix.at[f"Free Cash Flow (Mil {currency})", c_col] = format_with_commas(current_fcf_raw/UNIT)
            if cur_mcap_raw > 0: matrix.at["FCF Yield (%)", c_col] = format_with_commas((current_fcf_raw / cur_mcap_raw) * 100, is_currency=False)

    if need_save: save_portfolio()
    return matrix


# ==========================================
# 5. [Core] 일괄 동기화(Sync All) & 트래커 데이터 엔진
# ==========================================
def sync_all_cache():
    all_companies = st.session_state.owned_list + st.session_state.watched_list
    if not all_companies: return
    progress_text = "데이터 일괄 동기화 중... 잠시만 기다려주세요"
    my_bar = st.progress(0, text=progress_text)
    total = len(all_companies)
    for i, item in enumerate(all_companies):
        sym = item["symbol"]
        corp_code = item.get("corp_code", "")
        corp_name = item["name"]
        region = item["region"]
        my_bar.progress(i / total, text=f"[{i+1}/{total}] {corp_name} 데이터 수집 중...")
        silent_cache_fetch(corp_code, corp_name, sym, region)
        
        tk_sym = sym
        if region == "KR":
            if sym.endswith(".KS") or sym.endswith(".KQ"): tk_sym = sym
            else:
                try:
                    test_df = yf.download(f"{sym}.KS", period="1d", progress=False)
                    tk_sym = f"{sym}.KQ" if test_df.empty else f"{sym}.KS"
                except: tk_sym = f"{sym}.KS"
                
        try:
            df_curr = yf.download(tk_sym, period="1d", auto_adjust=False, progress=False)
            if not df_curr.empty:
                cols = df_curr.columns
                if isinstance(cols, pd.MultiIndex): df_curr.columns = cols.get_level_values(0)
                cur_price = safe_float(df_curr['Close'].iloc[-1])
                if cur_price > 0: st.session_state.price_cache[sym] = cur_price
        except: pass
    save_portfolio()
    st.session_state.last_portfolio_key = None 
    my_bar.progress(1.0, text="✅ 모든 데이터 동기화 완료!")

def calculate_moonki_tracker():
    current_year = datetime.now().year
    all_companies = st.session_state.owned_list + st.session_state.watched_list
    if not all_companies: return pd.DataFrame(), []
    
    tracker_data = []
    missing_data_symbols = [] 
    
    for item in all_companies:
        sym = item["symbol"]
        corp_name = item["name"]
        ctype = st.session_state.company_properties.get(sym, "일반")
        
        cached_entry = st.session_state.financial_cache.get(sym, {})
        overrides = st.session_state.manual_overrides.get(sym, {})

        def get_val(yr_str, key):
            rd = cached_entry.get(yr_str, {})
            ov = overrides.get(yr_str, {})
            return safe_float(ov.get(key, rd.get(key, 0)))

        if ctype in ["일반", "지주회사", "금융"]:
            rev_check = get_val("24Y", "rev") + get_val("23Y", "rev") + get_val("22Y", "rev") + get_val("24Y", "fcf")
            if rev_check == 0:
                missing_data_symbols.append(corp_name)
                continue
            
        cur_price = safe_float(st.session_state.manual_prices.get(sym, {}).get("Current", 0))
        if cur_price == 0: cur_price = safe_float(st.session_state.price_cache.get(sym, 0))
        
        if cur_price == 0:
            missing_data_symbols.append(corp_name)
            continue
            
        latest_sh = 0
        for yr in range(2014, current_year):
            sh_v = get_val(f"{str(yr)[2:]}Y", "shares")
            if sh_v > 0: latest_sh = sh_v
        if st.session_state.sim_shares.get(sym, None) is not None:
            latest_sh = st.session_state.sim_shares[sym]
            
        mcap_mil = (cur_price * latest_sh) / 1000000.0 if cur_price > 0 and latest_sh > 0 else 0.0
        if mcap_mil <= 0: continue
        
        if ctype in ["금융", "우선주"]:
            latest_dps = 0
            for yr in reversed(range(2014, current_year)):
                dps_v = get_val(f"{str(yr)[2:]}Y", "dps")
                if dps_v > 0: 
                    latest_dps = dps_v
                    break
            
            dps_5y = []
            for yr in range(current_year - 5, current_year):
                val = get_val(f"{str(yr)[2:]}Y", "dps")
                if get_val(f"{str(yr)[2:]}Y", "rev") > 0 or get_val(f"{str(yr)[2:]}Y", "fcf") != 0 or val > 0:
                    dps_5y.append(val)
            
            avg_dps_5y = sum(dps_5y) / len(dps_5y) if dps_5y else 0.0
            
            cur_yield = (latest_dps / cur_price) * 100 if cur_price > 0 else 0.0
            avg_5y_yield = (avg_dps_5y / cur_price) * 100 if cur_price > 0 else 0.0
            
        else:
            latest_fcf = 0
            for yr in reversed(range(2014, current_year)):
                fcf_v = get_val(f"{str(yr)[2:]}Y", "fcf")
                if fcf_v != 0: 
                    latest_fcf = fcf_v
                    break
            
            sim_fcf_val = st.session_state.sim_fcf.get(sym, None)
            current_fcf_raw = sim_fcf_val * 1000000.0 if sim_fcf_val is not None else latest_fcf
            cur_yield = (current_fcf_raw / 1000000.0 / mcap_mil) * 100 if mcap_mil > 0 else 0.0

            fcf_5y = []
            for yr in range(current_year - 5, current_year):
                if get_val(f"{str(yr)[2:]}Y", "rev") > 0:
                    fcf_5y.append(get_val(f"{str(yr)[2:]}Y", "fcf"))
                    
            avg_fcf_5y_mil = (sum(fcf_5y) / len(fcf_5y)) / 1000000.0 if fcf_5y else 0
            avg_5y_yield = (avg_fcf_5y_mil / mcap_mil) * 100 if mcap_mil > 0 else 0.0

        def get_cagr(years_back):
            rev_data = {}
            for yr in range(current_year - years_back, current_year):
                val = get_val(f"{str(yr)[2:]}Y", "rev")
                if val > 0: rev_data[yr] = val
            if not rev_data: return 0.0
            
            avail_yrs = sorted(rev_data.keys())
            l_yr = avail_yrs[-1]
            r_last = rev_data[l_yr]
            
            f_yr = l_yr - years_back
            if f_yr not in avail_yrs: f_yr = avail_yrs[0]
            r_first = rev_data[f_yr]
            
            if l_yr > f_yr and r_first > 0: return ((r_last / r_first) ** (1 / (l_yr - f_yr)) - 1) * 100
            return 0.0

        rev_10y_cagr = get_cagr(10) if ctype != "우선주" else 0.0
        rev_5y_cagr = get_cagr(5) if ctype != "우선주" else 0.0
        
        tracker_data.append({
            "Name": corp_name,
            "5Y Avg Yield (%)": avg_5y_yield,
            "Current Yield (%)": cur_yield,
            "Rev 10Y CAGR (%)": rev_10y_cagr,
            "Rev 5Y CAGR (%)": rev_5y_cagr
        })
        
    if not tracker_data: return pd.DataFrame(), missing_data_symbols
    df = pd.DataFrame(tracker_data)
    
    return df[["Name", "5Y Avg Yield (%)", "Current Yield (%)", "Rev 10Y CAGR (%)", "Rev 5Y CAGR (%)"]], missing_data_symbols


# ==========================================
# 6. [Interface] 사이드바 레이아웃 
# ==========================================
st.sidebar.header("⚙️ Global Search")
tab_kr, tab_us = st.sidebar.tabs(["🇰🇷 KR Search", "🇺🇸 US Search"])

with tab_kr:
    sq_kr = st.text_input("DART Name/Code", placeholder="삼성전자, KB금융")
    if sq_kr:
        f_codes = [c for c, i in dart_mapping.items() if sq_kr.lower() in i['name'].lower() or sq_kr == c]
        for c in f_codes[:5]:
            nm = dart_mapping[c]['name']
            c1, c2, c3 = st.columns([0.5, 0.25, 0.25])
            c1.write(f"**{nm}**")
            if c2.button("Hold", key=f"o_{c}_kr"):
                if not any(d['symbol'] == c for d in st.session_state.owned_list):
                    st.session_state.owned_list.insert(0, {"symbol": c, "name": nm, "region": "KR", "corp_code": dart_mapping[c]['corp_code']})
                    st.session_state.last_portfolio_key = None 
                    save_portfolio(); st.rerun()
            if c3.button("Watch", key=f"w_{c}_kr"):
                if not any(d['symbol'] == c for d in st.session_state.watched_list):
                    st.session_state.watched_list.insert(0, {"symbol": c, "name": nm, "region": "KR", "corp_code": dart_mapping[c]['corp_code']})
                    st.session_state.last_portfolio_key = None 
                    save_portfolio(); st.rerun()

with tab_us:
    sq_us = st.text_input("Yahoo Ticker/Name", placeholder="우선주는 티커입력 (예: 005935.KS)")
    if sq_us:
        try:
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={sq_us}"
            y_res = requests.get(url, headers=HEADERS, timeout=5).json()
            for q in y_res.get('quotes', [])[:5]:
                sym = q.get('symbol', '')
                if '.' not in sym and len(sym) <= 5: 
                    nm = q.get('shortname', sym)
                    c1, c2, c3 = st.columns([0.5, 0.25, 0.25])
                    c1.write(f"**{nm}** ({sym})")
                    if c2.button("Hold", key=f"o_{sym}_us"):
                        if not any(d['symbol'] == sym for d in st.session_state.owned_list):
                            st.session_state.owned_list.insert(0, {"symbol": sym, "name": nm, "region": "US", "corp_code": ""})
                            st.session_state.last_portfolio_key = None 
                            save_portfolio(); st.rerun()
                    if c3.button("Watch", key=f"w_{sym}_us"):
                        if not any(d['symbol'] == sym for d in st.session_state.watched_list):
                            st.session_state.watched_list.insert(0, {"symbol": sym, "name": nm, "region": "US", "corp_code": ""})
                            st.session_state.last_portfolio_key = None 
                            save_portfolio(); st.rerun()
                elif '.KS' in sym or '.KQ' in sym: 
                    nm = q.get('shortname', sym)
                    c1, c2, c3 = st.columns([0.5, 0.25, 0.25])
                    c1.write(f"**{nm}** ({sym})")
                    if c2.button("Hold", key=f"o_{sym}_kr_pref"):
                        if not any(d['symbol'] == sym for d in st.session_state.owned_list):
                            st.session_state.owned_list.insert(0, {"symbol": sym, "name": nm, "region": "KR", "corp_code": ""})
                            st.session_state.last_portfolio_key = None 
                            save_portfolio(); st.rerun()
                    if c3.button("Watch", key=f"w_{sym}_kr_pref"):
                        if not any(d['symbol'] == sym for d in st.session_state.watched_list):
                            st.session_state.watched_list.insert(0, {"symbol": sym, "name": nm, "region": "KR", "corp_code": ""})
                            st.session_state.last_portfolio_key = None 
                            save_portfolio(); st.rerun()
        except: pass

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Select to View")
st.sidebar.caption("💼 Owned")
for it in st.session_state.owned_list:
    if st.sidebar.button(f"📊 {it['name']} [{it['symbol']}]", key=f"btn_o_{it['symbol']}", use_container_width=True, type="primary"):
        st.session_state.selected_symbol, st.session_state.selected_corp_name, st.session_state.selected_region, st.session_state.selected_corp_code = it['symbol'], it['name'], it['region'], it.get('corp_code','')
        st.session_state.sim_fcf, st.session_state.sim_shares = {}, {}
        st.session_state.current_view = "Matrix"
        st.rerun()

st.sidebar.caption("📌 Watchlist")
for it in st.session_state.watched_list:
    if st.sidebar.button(f"📊 {it['name']} [{it['symbol']}]", key=f"btn_w_{it['symbol']}", use_container_width=True, type="primary"):
        st.session_state.selected_symbol, st.session_state.selected_corp_name, st.session_state.selected_region, st.session_state.selected_corp_code = it['symbol'], it['name'], it['region'], it.get('corp_code','')
        st.session_state.sim_fcf, st.session_state.sim_shares = {}, {}
        st.session_state.current_view = "Matrix"
        st.rerun()

with st.sidebar:
    st.markdown("---")
    st.subheader("↕️ Drag & Drop to Edit")
    orig_data = [{"header": "💼 Owned", "items": [f"{x['name']} [{x['symbol']}]" for x in st.session_state.owned_list]},
                 {"header": "📌 Watchlist", "items": [f"{x['name']} [{x['symbol']}]" for x in st.session_state.watched_list]},
                 {"header": "🗑️ Trash", "items": []}]
    new_data = sort_items(orig_data, multi_containers=True)
    if new_data != orig_data:
        all_combined = st.session_state.owned_list + st.session_state.watched_list
        def unpack_item(s):
            sym = s.split(" [")[1].replace("]","")
            return next((i for i in all_combined if i["symbol"] == sym), {"symbol": sym, "name": s.split(" [")[0], "region": "US" if not sym.isdigit() else "KR", "corp_code": ""})
        st.session_state.owned_list = [unpack_item(i) for i in new_data[0]['items']]
        st.session_state.watched_list = [unpack_item(i) for i in new_data[1]['items']]
        st.session_state.last_portfolio_key = None 
        save_portfolio(); st.rerun()

    st.markdown("---")
    if st.button("🏆 Return Yield Tracker", use_container_width=True):
        st.session_state.current_view = "Tracker"
        st.rerun()

# ==========================================
# 7. 메인 우측 화면 분기 출력
# ==========================================
if st.session_state.current_view == "Matrix":
    if st.session_state.selected_symbol:
        c1, c2, c3 = st.columns([0.6, 0.25, 0.15])
        with c3:
            st.write(""); st.write("")
            edit_mode = st.toggle("🛠️ Raw Data Edit", key="edit_toggle")
            
        with c2:
            st.write("")
            options = ["일반", "지주회사", "금융", "우선주"]
            ctype = st.session_state.company_properties.get(st.session_state.selected_symbol, "일반")
            new_ctype = st.selectbox("🏢 기업 속성 설정", options, index=options.index(ctype), disabled=not edit_mode, label_visibility="collapsed")
            
            if new_ctype != ctype:
                st.session_state.company_properties[st.session_state.selected_symbol] = new_ctype
                st.session_state.financial_cache[st.session_state.selected_symbol] = {} 
                st.session_state.last_portfolio_key = None 
                save_portfolio()
                st.rerun()
                
        with c1:
            st.header(f"🖥️ [{st.session_state.selected_corp_name}] ({st.session_state.selected_region}) Financial Matrix")
        
        with st.spinner("Executing Strict Vanilla Engine..."):
            m_df = fetch_matrix_final(st.session_state.selected_corp_code, st.session_state.selected_corp_name, st.session_state.selected_symbol, st.session_state.selected_region)
        
        ed_df = st.data_editor(m_df, use_container_width=True, height=880, key="grid_ed")

        if "grid_ed" in st.session_state and st.session_state.grid_ed.get("edited_rows"):
            edits = st.session_state.grid_ed["edited_rows"]
            updated_p, updated_f = False, False
            for row_idx, col_edits in edits.items():
                row_label = m_df.index[row_idx] if isinstance(row_idx, int) and row_idx < len(m_df.index) else str(row_idx)
                for col_name, raw_input in col_edits.items():
                    val = evaluate_formula(raw_input)
                    if val is None: continue
                    if "Stock Price" in row_label:
                        if st.session_state.selected_symbol not in st.session_state.manual_prices: st.session_state.manual_prices[st.session_state.selected_symbol] = {}
                        st.session_state.manual_prices[st.session_state.selected_symbol][col_name if col_name == "Current" else col_name.replace("Y","년")] = val
                        updated_p = True
                    elif col_name == "Current":
                        if "Free Cash Flow" in row_label or "Net Income" in row_label: st.session_state.sim_fcf[st.session_state.selected_symbol] = val; updated_f = True
                        elif "Shares Outstanding" in row_label: st.session_state.sim_shares[st.session_state.selected_symbol] = val; updated_f = True
                    elif edit_mode and col_name != "Current":
                        metric_key = None
                        if "Shares Outstanding" in row_label: metric_key = "shares"
                        elif "Revenue" in row_label or "Operating Revenue" in row_label: metric_key = "rev"
                        elif "Gross Profit" in row_label or "Net Interest Income" in row_label: metric_key = "gp"
                        elif "Operating Income" in row_label: metric_key = "op"
                        elif "Free Cash Flow" in row_label or "Net Income" in row_label: metric_key = "fcf"
                        elif "Total Dividends" in row_label: metric_key = "div_total"
                        elif "DPS" in row_label: metric_key = "dps"
                        if metric_key:
                            val_to_save = val * 1000000.0 if "Mil" in row_label else val
                            sym = st.session_state.selected_symbol
                            if sym not in st.session_state.manual_overrides: st.session_state.manual_overrides[sym] = {}
                            if col_name not in st.session_state.manual_overrides[sym]: st.session_state.manual_overrides[sym][col_name] = {}
                            st.session_state.manual_overrides[sym][col_name][metric_key] = val_to_save
                            st.session_state.last_portfolio_key = None 
                            updated_p = True
            if updated_p: save_portfolio(); st.rerun()
            elif updated_f: st.rerun()
    else:
        st.markdown("<br><br><br><br><h3 style='text-align: center; color: gray;'>👈 좌측 사이드바에서 종목을 선택해 주세요.</h3>", unsafe_allow_html=True)

elif st.session_state.current_view == "Tracker":
    r_col1, r_col2 = st.columns([0.8, 0.2])
    with r_col1:
        st.header("🏆 Return Yield Tracker")
    with r_col2:
        st.write("")
        if st.button("🔄 일괄 동기화 (Sync All)", use_container_width=True, type="primary"):
            sync_all_cache()
            st.rerun()
    
    with st.spinner("Loading Tracker instantly from Local Cache..."):
        current_portfolio_key = str([x["symbol"] for x in st.session_state.owned_list + st.session_state.watched_list])
        if "last_ranking_df" not in st.session_state or st.session_state.get("last_portfolio_key") != current_portfolio_key:
            df, missing = calculate_moonki_tracker()
            st.session_state.last_ranking_df = (df, missing)
            st.session_state.last_portfolio_key = current_portfolio_key
            
    rank_df, missing_symbols = st.session_state.last_ranking_df
    
    if missing_symbols:
        st.warning(f"⚠️ 로컬 데이터가 누락된 기업: **{', '.join(missing_symbols)}** (우측 상단의 '🔄 일괄 동기화' 버튼을 눌러주세요!)")
        
    if not rank_df.empty:
        sort_target = st.radio(
            "Sort by:",
            ["5Y Avg Yield (%)", "Current Yield (%)", "Rev 10Y CAGR (%)", "Rev 5Y CAGR (%)"],
            horizontal=True,
            label_visibility="collapsed"
        )
        
        rank_df = rank_df.sort_values(by=sort_target, ascending=False).reset_index(drop=True)
        rank_df.index = rank_df.index + 1
        
        st.dataframe(
            rank_df, 
            use_container_width=True, 
            height=750,
            column_config={
                "5Y Avg Yield (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Current Yield (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Rev 10Y CAGR (%)": st.column_config.NumberColumn(format="%.1f%%"),
                "Rev 5Y CAGR (%)": st.column_config.NumberColumn(format="%.1f%%")
            }
        )
    else:
        st.info("현재 산정할 기업이 없습니다. 관심 기업 추가 후 '일괄 동기화' 버튼을 눌러주세요.")