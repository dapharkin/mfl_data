import streamlit as st
import pandas as pd
import requests
import urllib.parse
import matplotlib.pyplot as plt
import matplotlib.cm as cm


# --- PAGE CONFIG ---
st.set_page_config(page_title="MFL Scouting Viewer", layout="wide")

# --- AUTH ---
bearer_token = st.secrets["auth"]["bearer_token"]

headers = {
    "Authorization": f"Bearer {bearer_token}",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

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
st.sidebar.title("🔎 Scouting Filters")
age_range = st.sidebar.slider("Age range", 16, 42, (16, 42))
min_age, max_age = age_range
ovr_range = st.sidebar.slider("OVR range", 25, 99, (55, 66))
min_ovr, max_ovr = ovr_range
division = st.sidebar.selectbox("Division", list(range(1, 10)), index=7)  # default to 8



all_positions = ["GK", "RB", "LB", "CB", "RWB", "LWB", "CDM", "CM", "CAM", "RM", "LM", "RW", "LW", "CF", "ST"]

with_auto_accept = st.sidebar.checkbox("Auto Accept", value=False)

selected_positions = st.sidebar.multiselect("Positions", all_positions, default=[])

if st.sidebar.button("🔁 Refresh Results"):
    st.session_state.scouting_players = []
    st.session_state.scouting_last_id = None
    st.rerun()

# --- SESSION STATE ---
if "scouting_players" not in st.session_state:
    st.session_state.scouting_players = []
if "scouting_last_id" not in st.session_state:
    st.session_state.scouting_last_id = None

# --- API FETCHING ---
def get_scouting_url(before_id=None):
    base = (
        "https://z519wdyajg.execute-api.us-east-1.amazonaws.com/prod/players"
        f"?limit=100&sorts=metadata.overall&sortsOrders=DESC"
        f"&overallMin={min_ovr}&overallMax={max_ovr}"
        f"&excludingMflOwned=true&isFreeAgent=true&offerStatuses=2"
        f"&ownerLastActivity=lastWeek&ageMin={min_age}&ageMax={max_age}"
        f"&offerDivisionAccepted={division}"
    )
    if with_auto_accept:
        base += "&offerAutoAccept=true"
    if selected_positions:
        position_param = urllib.parse.quote(",".join(selected_positions))
        base += f"&positions={position_param}"
    if before_id:
        base += f"&beforePlayerId={before_id}"
    return base

def fetch_scouting_list(before_id=None):
    url = get_scouting_url(before_id)
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        st.error(f"❌ Failed to fetch scouting data: {response.status_code}")
        return []
    return response.json()

# --- MAIN APP ---
st.title("🧭 MFL Scouting Viewer")

if len(st.session_state.scouting_players) == 0:
    for _ in range(3):  # initial load with 3 batches
        new_batch = fetch_scouting_list(before_id=st.session_state.scouting_last_id)
        if not new_batch:
            break
        st.session_state.scouting_players.extend(new_batch)
        st.session_state.scouting_last_id = new_batch[-1]["id"]

players = st.session_state.scouting_players
df = pd.json_normalize(players)

if not df.empty:
    df["player_id"] = df["id"]
    df["current_OVR"] = df["metadata.overall"]
    df["Name"] = df["metadata.firstName"] + " " + df["metadata.lastName"]
    df["positions_raw"] = df["metadata.positions"]
    df["positions"] = df["positions_raw"].apply(lambda x: ", ".join(x) if isinstance(x, list) else x)
    df["age"] = df["metadata.age"]
    df["club"] = df["ownedBy.name"].fillna("")

    stat_map = {
        "PAC": "pace", "SHO": "shooting", "PAS": "passing",
        "DRI": "dribbling", "DEF": "defense", "PHY": "physical"
    }
    for short, long in stat_map.items():
        df[short] = df[f"metadata.{long}"]

    results = []
    for _, row in df.iterrows():
        stats = {k: row.get(k, 0) for k in stat_map.keys()}
        pos_list = row["positions_raw"] if isinstance(row["positions_raw"], list) else []
        primary, secondary, tertiary = (pos_list + [None, None, None])[:3]
        overall = row["current_OVR"]
        result = calculate_best_alt_ovr(stats, primary, secondary, tertiary, overall)
        results.append(result)

    df["best_alt_position"] = [r["best_alt_position"] for r in results]
    df["best_alt_OVR"] = [r["best_alt_ovr"] for r in results]
    df["OVR_delta"] = [r["delta_to_overall"] for r in results]

    def get_div_share(preferences, div):
        if isinstance(preferences, list):
            for pref in preferences:
                if pref.get("div") == div:
                    return f"{pref.get('minRevenueShare', 0) / 100:.0f}%"
        return "–"

    def get_div_clause(clauses):
        if isinstance(clauses, list):
            for clause in clauses:
                if clause.get("type") == "MINIMUM_PLAYING_TIME":
                    matches = clause.get("nbMatches")
                    penalty = clause.get("revenueSharePenalty", 0)
                    if matches is not None:
                        return f"{matches} (+{penalty / 100:.0f}%)"
        return "–"

    df["Min Clause"] = df["offerPreferences"].apply(lambda prefs: get_div_share(prefs, division))
    df["Min Matches"] = df["offerClauses"].apply(get_div_clause)
    df["MFL"] = df["player_id"].apply(lambda x: f"https://app.playmfl.com/players/{x}")
    df["Info"] = df["player_id"].apply(lambda x: f"https://mflplayer.info/player/{x}")

    display_df = df[[
        "Name", "age", "positions", "current_OVR", "best_alt_position", "best_alt_OVR",  "OVR_delta",
         "club", "PAC", "SHO", "PAS", "DRI", "DEF", "PHY",
        "Min Clause", "Min Matches", "MFL", "Info"
    ]].sort_values(by="OVR_delta", ascending=False)

    display_df.columns = [
        "Name", "Age","Positions", "OVR", "Best Pos","Best OVR", "Pos Gain",
        "Owner",  "PAC", "SHO", "PAS", "DRI", "DEF", "PHY",
        "Min Clause (%)", "Min Matches", "MFL", "Info"
    ]

    # Helper function for color mapping
    def hex_gradient(val, min_val=1, max_val=10):
        # Clamp the value within the expected range
        val = max(min_val, min(val, max_val))
        ratio = (val - min_val) / (max_val - min_val)

        # Start: #70db70 (112, 219, 112) — light green
        # End:   #004d00 (0, 77, 0)     — dark green
        start_rgb = (112, 219, 112)
        end_rgb = (0, 77, 0)

        # Linear interpolation
        r = int(start_rgb[0] + (end_rgb[0] - start_rgb[0]) * ratio)
        g = int(start_rgb[1] + (end_rgb[1] - start_rgb[1]) * ratio)
        b = int(start_rgb[2] + (end_rgb[2] - start_rgb[2]) * ratio)

        return f"background-color: rgb({r}, {g}, {b}); color: white;"

    def delta_color(val):
        if val > 0:
            return hex_gradient(val)
        return ""  # Default background for 0


    styled_df = display_df.style.applymap(delta_color, subset=["Pos Gain"])
    st.dataframe(styled_df, use_container_width=True, hide_index=True)



    if st.button("➕ Load More Results"):
        new_batch = fetch_scouting_list(before_id=st.session_state.scouting_last_id)
        if new_batch:
            st.session_state.scouting_players.extend(new_batch)
            st.session_state.scouting_last_id = new_batch[-1]["id"]
            st.rerun()
        else:
            st.info("✅ No more players available.")
else:
    st.warning("No results returned from scouting API.")