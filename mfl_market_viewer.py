import streamlit as st
import pandas as pd
import requests
import urllib.parse
import hashlib
import json
import os
import time

# --- PAGE CONFIG ---
st.set_page_config(page_title="MFL Marketplace Viewer", layout="wide")

# --- SETTINGS ---
bearer_token = st.secrets["auth"]["bearer_token"]


headers = {
    "Authorization": f"Bearer {bearer_token}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

# --- FLOOR CACHE ---
CACHE_PATH = "floor_cache.json"
CACHE_EXPIRY_SECONDS = 3600  # 1 hour

if os.path.exists(CACHE_PATH):
    with open(CACHE_PATH, "r") as f:
        try:
            floor_price_cache = json.load(f)
        except json.JSONDecodeError:
            floor_price_cache = {}
else:
    floor_price_cache = {}

def save_floor_cache():
    with open(CACHE_PATH, "w") as f:
        json.dump(floor_price_cache, f)

def generate_floor_key(age, ovr, position):
    return hashlib.md5(f"{age}-{ovr}-{position}".encode()).hexdigest()

def get_floor_price(age, ovr, position):
    if age is None or ovr is None or position is None:
        return None

    key = generate_floor_key(age, ovr, position)
    now = time.time()

    if key in floor_price_cache:
        cached = floor_price_cache[key]
        if now - cached.get("timestamp", 0) < CACHE_EXPIRY_SECONDS:
            return cached["price"]

    url = (
        f"https://z519wdyajg.execute-api.us-east-1.amazonaws.com/prod/listings"
        f"?limit=1&type=PLAYER&sorts=listing.price&sortsOrders=ASC&status=AVAILABLE"
        f"&ageMin={age}&ageMax={age}&overallMin={ovr}&overallMax={ovr}&positions={position}&view=full"
    )

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data and isinstance(data, list) and "price" in data[0]:
                price = data[0]["price"]
                floor_price_cache[key] = {"price": price, "timestamp": now}
                save_floor_cache()
                return price
    except:
        pass

    floor_price_cache[key] = {"price": None, "timestamp": now}
    save_floor_cache()
    return None

# --- LOAD DATA FILES ---
weightings_df = pd.read_csv("overall_weightings.csv")
familiarity_df = pd.read_csv("position_familiarity.csv")

weightings_dict = (
    weightings_df
    .pivot(index="Position", columns="Attribute", values="Weighting")
    .fillna(0)
    .astype(float)
    .to_dict(orient="index")
)

familiarity_dict = {
    (row["Primary_position"], row["Assigned_position"]): row["Position_Penalty"]
    for _, row in familiarity_df.iterrows()
}

def calculate_best_alt_ovr(player_stats, primary, secondary, tertiary, overall):
    best_alt_ovr = None
    best_position = None

    for pos in weightings_dict.keys():
        weights = weightings_dict[pos]
        weighted_sum = sum(player_stats.get(attr, 0) * weights.get(attr, 0) for attr in weights)
        base_ovr = weighted_sum / 100

        if pos == primary:
            penalty = 0
        elif pos == secondary or pos == tertiary:
            penalty = -1
        else:
            penalty = familiarity_dict.get((primary, pos), 0)

        alt_ovr = max(0, base_ovr + penalty)

        if pos != primary:
            if best_alt_ovr is None or alt_ovr > best_alt_ovr:
                best_alt_ovr = alt_ovr
                best_position = pos

    if best_alt_ovr is None or best_alt_ovr <= overall:
        return {
            "best_alt_position": primary,
            "best_alt_ovr": overall,
            "delta_to_overall": 0
        }

    return {
        "best_alt_position": best_position,
        "best_alt_ovr": int(round(best_alt_ovr)),
        "delta_to_overall": int(round(best_alt_ovr - overall))
    }

# --- SIDEBAR FILTERS ---
st.sidebar.title("🔎 Filters")
age_range = st.sidebar.slider("Age range", 16, 42, (16, 28))
min_age, max_age = age_range
ovr_range = st.sidebar.slider("OVR range", 25, 99, (55, 66))
min_ovr, max_ovr = ovr_range
price_range = st.sidebar.slider("Price Range ($)", 1, 100, (1, 10))
min_price, max_price = price_range
is_free_agent = st.sidebar.checkbox("Free Agent Only", value=False)




all_positions = ["GK", "RB", "LB", "CB", "RWB", "LWB", "CDM", "CM", "CAM", "RM", "LM", "RW", "LW", "CF", "ST"]
selected_positions = st.sidebar.multiselect("Positions", all_positions, default=[])

# --- SESSION STATE ---
if "listings" not in st.session_state:
    st.session_state.listings = []
if "last_id" not in st.session_state:
    st.session_state.last_id = None

# --- API FETCHING ---
def get_api_url(before_id=None):
    base = (
        "https://z519wdyajg.execute-api.us-east-1.amazonaws.com/prod/listings"
        f"?limit=25&type=PLAYER&sorts=metadata.overall&sortsOrders=DESC&status=AVAILABLE"
        f"&overallMin={min_ovr}&overallMax={max_ovr}&priceMin={min_price}&priceMax={max_price}&ageMin={min_age}&ageMax={max_age}&view=full"
    )
    if is_free_agent:
        base += "&isFreeAgent=true"
    if selected_positions:
        position_param = urllib.parse.quote(",".join(selected_positions))
        base += f"&positions.name={position_param}"
    if before_id:
        base += f"&beforeListingId={before_id}"
    return base

def load_listings(before_id=None):
    url = get_api_url(before_id)
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        st.error(f"❌ Failed to load listings: {response.status_code}")
        return []
    return response.json()

# --- MAIN APP ---
st.title("📋 MFL Marketplace Viewer")

if st.sidebar.button("🔁 Refresh Results"):
    st.session_state.listings = []
    st.session_state.last_id = None
    st.rerun()

if len(st.session_state.listings) == 0:
    last_id = None
    for _ in range(6):
        new_batch = load_listings(before_id=last_id)
        if not new_batch:
            break
        st.session_state.listings.extend(new_batch)
        last_id = new_batch[-1]["listingResourceId"]
    st.session_state.last_id = last_id

df = pd.json_normalize(st.session_state.listings)

if not df.empty:
    df["player_id"] = df["player.id"]
    df["price"] = df["price"].astype(float)
    df["current_OVR"] = df["player.metadata.overall"]
    df["Name"] = df["player.metadata.firstName"] + " " + df["player.metadata.lastName"]
    df["positions_raw"] = df["player.metadata.positions"]
    df["positions"] = df["positions_raw"].apply(lambda x: ", ".join(x) if isinstance(x, list) else x)
    df["age"] = df["player.metadata.age"]
    df["club"] = df["player.activeContract.club.name"]
    df["MFL"] = df["player_id"].apply(lambda x: f"https://app.playmfl.com/players/{x}")
    df["Info"] = df["player_id"].apply(lambda x: f"https://mflplayer.info/player/{x}")

    stat_map = {
        "PAC": "pace",
        "SHO": "shooting",
        "PAS": "passing",
        "DRI": "dribbling",
        "DEF": "defense",
        "PHY": "physical"
    }
    for short, long in stat_map.items():
        df[short] = df[f"player.metadata.{long}"]

    results = []
    for _, row in df.iterrows():
        stats = {k: row.get(k, 0) for k in stat_map.keys()}
        pos_list = row["positions_raw"] if isinstance(row["positions_raw"], list) else []
        primary, secondary, tertiary = (pos_list + [None, None, None])[:3]
        overall = row["current_OVR"]
        age = row["age"]

        result = calculate_best_alt_ovr(stats, primary, secondary, tertiary, overall)

        result["floor_primary"] = get_floor_price(age, overall, primary)
        result["floor_alt"] = get_floor_price(age, result["best_alt_ovr"], result["best_alt_position"])

        results.append(result)

    df["best_alt_position"] = [r["best_alt_position"] for r in results]
    df["best_alt_OVR"] = [r["best_alt_ovr"] for r in results]
    df["OVR_delta"] = [r["delta_to_overall"] for r in results]
    df["floor_primary"] = [r.get("floor_primary", None) for r in results]
    df["floor_alt"] = [r.get("floor_alt", None) for r in results]

    st.session_state.last_id = df.iloc[-1]["listingResourceId"]

    display_df = df[[
        "Name", "age","positions","current_OVR", "best_alt_position", "best_alt_OVR", "OVR_delta",
         "price", "floor_primary", "floor_alt", "club",  "PAC", "SHO", "PAS", "DRI", "DEF", "PHY", "MFL", "Info"
    ]].sort_values(by="OVR_delta", ascending=False)

    display_df.columns = [
        "Name", "Age","Positions", "OVR", "Best Pos","Best OVR", "Δ",
        "Price", "Floor (OVR)", "Floor (Alt)", "Club",  "PAC", "SHO", "PAS", "DRI", "DEF", "PHY", "MFL", "Info"
    ]

    styled_df = display_df.style.background_gradient(
    subset=["Δ"],
    cmap="Greens"  # Options: "Greens", "Blues", "Oranges", etc.
    )
    st.dataframe(styled_df, use_container_width=True, hide_index=True)


    if st.button("➕ Load More Listings"):
        new_batch = load_listings(before_id=st.session_state.last_id)
        if new_batch:
            st.session_state.listings.extend(new_batch)
            st.rerun()
        else:
            st.info("✅ No more listings available.")
else:
    st.warning("No listings returned from API.")
