from pybaseball import playerid_lookup
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import scipy.stats as stats
import time
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

CACHE_PATH = Path(__file__).with_name("team_stats_cache.json")
EASTERN_TZ = ZoneInfo("America/New_York")
SEASON_STATS_PATH = Path(__file__).with_name("season_stats_cache.json")
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

# Mapping from team abbreviations to full names for pybaseball
TEAM_ABBR_TO_NAME = {
    'ARI': 'Arizona Diamondbacks',
    'ATL': 'Atlanta Braves',
    'BAL': 'Baltimore Orioles',
    'BOS': 'Boston Red Sox',
    'CHC': 'Chicago Cubs',
    'CHW': 'Chicago White Sox',
    'CIN': 'Cincinnati Reds',
    'CLE': 'Cleveland Guardians',
    'COL': 'Colorado Rockies',
    'DET': 'Detroit Tigers',
    'HOU': 'Houston Astros',
    'KCR': 'Kansas City Royals',
    'LAA': 'Los Angeles Angels',
    'LAD': 'Los Angeles Dodgers',
    'MIA': 'Miami Marlins',
    'MIL': 'Milwaukee Brewers',
    'MIN': 'Minnesota Twins',
    'NYM': 'New York Mets',
    'NYY': 'New York Yankees',
    'OAK': 'Oakland Athletics',
    'ATH': 'Athletics',
    'PHI': 'Philadelphia Phillies',
    'PIT': 'Pittsburgh Pirates',
    'SDP': 'San Diego Padres',
    'SEA': 'Seattle Mariners',
    'SFG': 'San Francisco Giants',
    'STL': 'St. Louis Cardinals',
    'TBR': 'Tampa Bay Rays',
    'TEX': 'Texas Rangers',
    'TOR': 'Toronto Blue Jays',
    'WSN': 'Washington Nationals'
}
NAME_TO_TEAM_ABBR = {name: abbr for abbr, name in TEAM_ABBR_TO_NAME.items()}

# All known aliases → canonical abbreviation used throughout this script
ABBR_ALIASES = {
    # Short forms the schedule/stats API sometimes returns
    'KC':  'KCR',
    'SD':  'SDP',
    'SF':  'SFG',
    'TB':  'TBR',
    'WSH': 'WSN',
    'CWS': 'CHW',
    # Long forms the stats API sometimes returns
    'KCR': 'KCR',
    'SDP': 'SDP',
    'SFG': 'SFG',
    'TBR': 'TBR',
    'WSN': 'WSN',
    'CHW': 'CHW',
}

def normalize_abbr(abbr: str) -> str:
    """Map any abbreviation variant to the canonical form used in this script."""
    return ABBR_ALIASES.get(abbr, abbr)


def load_team_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_team_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def get_current_et():
    return datetime.now(EASTERN_TZ)


def cache_needs_refresh(last_updated_str: str) -> bool:
    if not last_updated_str:
        return True

    try:
        last_updated = datetime.fromisoformat(last_updated_str).astimezone(EASTERN_TZ)
    except Exception:
        return True

    now_et = get_current_et()
    today_et = now_et.date()
    last_date = last_updated.date()
    refresh_threshold = datetime.combine(today_et, datetime.min.time(), tzinfo=EASTERN_TZ).replace(hour=15)

    if now_et - last_updated > timedelta(days=14):
        return True

    if now_et >= refresh_threshold:
        return last_date < today_et
    return last_date < today_et - timedelta(days=1)


def refresh_team_stats(team_abbr: str):
    try:
        now_et = get_current_et()
        start_date = (now_et - timedelta(days=14)).strftime("%Y-%m-%d")
        end_date = now_et.strftime("%Y-%m-%d")

        url = (
            "https://statsapi.mlb.com/api/v1/teams/stats?"
            f"season=2026&group=hitting&sportId=1&gameType=R&startDate={start_date}&endDate={end_date}"
        )
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        response.raise_for_status()
        data = response.json()

        splits = data.get('stats', [{}])[0].get('splits', [])
        if not splits:
            return None

        for split in splits:
            team = split.get('team', {})
            # Normalize the abbreviation from the API before comparing
            if normalize_abbr(team.get('abbreviation', '')) != team_abbr:
                continue

            stat = split.get('stat', {})
            plate_app = float(stat.get('plateAppearances', 0) or 0)
            if plate_app <= 0:
                return None

            strikeouts = float(stat.get('strikeOuts', 0) or 0)
            k_pct = strikeouts / plate_app

            ops_value = stat.get('ops')
            try:
                ops = float(ops_value) if ops_value is not None else 0.0
            except Exception:
                ops = 0.0

            # Derive wOBA from basic batting counts when advanced values are unavailable
            woba_value = stat.get('weightedOnBaseAverage')
            if woba_value is not None:
                try:
                    woba = float(woba_value)
                except Exception:
                    woba = 0.315
            else:
                bb = float(stat.get('baseOnBalls', 0) or 0)
                ibb = float(stat.get('intentionalWalks', 0) or 0)
                hbp = float(stat.get('hitByPitch', 0) or 0)
                hits = float(stat.get('hits', 0) or 0)
                doubles = float(stat.get('doubles', 0) or 0)
                triples = float(stat.get('triples', 0) or 0)
                home_runs = float(stat.get('homeRuns', 0) or 0)
                at_bats = float(stat.get('atBats', 0) or 0)
                sac_flies = float(stat.get('sacFlies', 0) or 0)
                singles = max(0.0, hits - doubles - triples - home_runs)
                ubb = max(0.0, bb - ibb)

                woba_numerator = (
                    0.7 * ubb
                    + 0.72 * hbp
                    + 0.88 * singles
                    + 1.24 * doubles
                    + 1.56 * triples
                    + 2.04 * home_runs
                )
                woba_denominator = at_bats + ubb + hbp + sac_flies
                woba = float(woba_numerator / woba_denominator) if woba_denominator > 0 else 0.315

            return {
                "K%": k_pct,
                "wOBA": woba,
                "OPS": ops,
                "last_updated": get_current_et().isoformat()
            }

        return None
    except Exception:
        return None


def get_opponent_stats(team_abbr: str):
    # Always normalize to canonical form before any lookup
    team_abbr = normalize_abbr(team_abbr)

    cache = load_team_cache()
    team_entry = cache.get(team_abbr)

    if team_entry and not cache_needs_refresh(team_entry.get('last_updated', '')):
        return team_entry

    fresh = refresh_team_stats(team_abbr)
    if fresh:
        cache[team_abbr] = fresh
        save_team_cache(cache)
        return fresh

    # Use season stats as fallback instead of league defaults
    season_stats = get_season_stats(team_abbr)
    if season_stats:
        return {**season_stats, "last_updated": "season_fallback"}
    
    print(f"No stats available for {team_abbr} from any source, cannot generate accurate projection")
    return None


def print_cache_status(team_abbr: str, entry: dict):
    last_updated = entry.get('last_updated', 'unknown')
    if last_updated == 'fallback':
        print(f"Cache: no fresh data for {team_abbr}, using league default values")
        return
    if last_updated == 'season_fallback':
        print(f"Cache: no fresh data for {team_abbr}, using 2026 season stats")
        return

    try:
        last_dt = datetime.fromisoformat(last_updated).astimezone(EASTERN_TZ)
        print(f"Cache last refreshed for {team_abbr} (14-day stats): {last_dt.strftime('%Y-%m-%d %I:%M %p ET')}")
    except Exception:
        print(f"Cache last updated for {team_abbr}: {last_updated}")


def load_season_stats():
    """Load 2026 season-to-date team batting stats as fallback."""
    if SEASON_STATS_PATH.exists():
        try:
            return json.loads(SEASON_STATS_PATH.read_text())
        except Exception:
            pass
    return {}


def save_season_stats(stats):
    SEASON_STATS_PATH.write_text(json.dumps(stats, indent=2))


def refresh_season_stats():
    """Fetch current season team stats only from MLB Stats API."""
    try:
        url = "https://statsapi.mlb.com/api/v1/teams/stats?season=2026&group=hitting"
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        response.raise_for_status()
        data = response.json()

        stats = {}
        for split in data.get('stats', [{}])[0].get('splits', []):
            raw_abbr = split.get('team', {}).get('abbreviation')
            abbr = normalize_abbr(raw_abbr) if raw_abbr else None
            stat = split.get('stat', {})
            if abbr:
                entry = {
                    "K%": float(stat.get('strikeoutPercentage', 24.0)) / 100,
                    "wOBA": float(stat.get('weightedOnBaseAverage', 0.315)),
                    "OPS": float(stat.get('onBasePlusSlugging', 0.720)),
                    "last_updated": get_current_et().isoformat()
                }
                # Store under canonical key only
                stats[abbr] = entry
        if stats:
            print("Fetched season stats from MLB Stats API")
            return stats
    except Exception as e:
        print(f"MLB Stats API failed: {e}")

    print("No season stats available from any source")
    return {}


def get_season_stats(team_abbr: str):
    """Get season stats for a team, refreshing if needed."""
    stats = load_season_stats()
    team_entry = stats.get(team_abbr)

    # Refresh daily at 3pm ET or if no data
    now_et = get_current_et()
    needs_refresh = not team_entry or (
        now_et.hour >= 15 and 
        datetime.fromisoformat(team_entry.get('last_updated', '2026-01-01')).date() < now_et.date()
    )

    if needs_refresh:
        fresh_stats = refresh_season_stats()
        if fresh_stats:
            save_season_stats(fresh_stats)
            return fresh_stats.get(team_abbr)
    
    return team_entry


def get_starting_pitchers_today():
    """Get probable and confirmed starting pitchers for today with their opponents using MLB Stats API."""
    try:
        today = get_current_et().strftime("%Y-%m-%d")

        print(f"Fetching schedule for {today}...")
        url = (
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}"
            "&hydrate=team(name),probablePitcher,person"
        )
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        response.raise_for_status()
        data = response.json()

        pitcher_opponents = []

        for date in data.get('dates', []):
            for game in date.get('games', []):
                game_pk = game.get('gamePk')
                game_status = game.get('status', {}).get('abstractGameState', 'Scheduled')
                print(f"Game {game_pk} status: {game_status}")

                # Get team abbreviations safely
                home_team_info = game.get('teams', {}).get('home', {}).get('team', {})
                away_team_info = game.get('teams', {}).get('away', {}).get('team', {})
                home_team = home_team_info.get('abbreviation') or home_team_info.get('name', 'Unknown')
                away_team = away_team_info.get('abbreviation') or away_team_info.get('name', 'Unknown')

                # Home team probable pitcher
                home_probable = game.get('teams', {}).get('home', {}).get('probablePitcher')
                if home_probable:
                    pitcher_id = home_probable.get('id') or home_probable.get('person', {}).get('id')
                    pitcher_name = (
                        home_probable.get('fullName')
                        or home_probable.get('person', {}).get('fullName')
                        or 'unknown'
                    )
                    print(f"Found probable home pitcher: {pitcher_name} vs {away_team}")
                    if pitcher_id and (pitcher_id, away_team) not in [(p, t) for p, t, _ in pitcher_opponents]:
                        pitcher_opponents.append((pitcher_id, away_team, pitcher_name))

                # Away team probable pitcher
                away_probable = game.get('teams', {}).get('away', {}).get('probablePitcher')
                if away_probable:
                    pitcher_id = away_probable.get('id') or away_probable.get('person', {}).get('id')
                    pitcher_name = (
                        away_probable.get('fullName')
                        or away_probable.get('person', {}).get('fullName')
                        or 'unknown'
                    )
                    print(f"Found probable away pitcher: {pitcher_name} vs {home_team}")
                    if pitcher_id and (pitcher_id, home_team) not in [(p, t) for p, t, _ in pitcher_opponents]:
                        pitcher_opponents.append((pitcher_id, home_team, pitcher_name))

                # For live or final games, also get confirmed starters from boxscore
                if game_status in ['Live', 'Final'] and game_pk:
                    print(f"Fetching boxscore for live/final game {game_pk}")
                    try:
                        boxscore_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
                        box_response = requests.get(boxscore_url, headers=REQUEST_HEADERS, timeout=20)
                        box_response.raise_for_status()
                        box_data = box_response.json()

                        # Home team starter (first pitcher in the list)
                        home_pitchers = box_data.get('teams', {}).get('home', {}).get('pitchers', [])
                        if home_pitchers:
                            starter_id = home_pitchers[0]
                            if (starter_id, away_team) not in [(p, t) for p, t, _ in pitcher_opponents]:
                                pitcher_opponents.append((starter_id, away_team, f'ID:{starter_id}'))
                                print(f"Added confirmed home starter {starter_id} vs {away_team}")

                        # Away team starter (first pitcher in the list)
                        away_pitchers = box_data.get('teams', {}).get('away', {}).get('pitchers', [])
                        if away_pitchers:
                            starter_id = away_pitchers[0]
                            if (starter_id, home_team) not in [(p, t) for p, t, _ in pitcher_opponents]:
                                pitcher_opponents.append((starter_id, home_team, f'ID:{starter_id}'))
                                print(f"Added confirmed away starter {starter_id} vs {home_team}")

                    except Exception as e:
                        print(f"Error fetching boxscore for game {game_pk}: {e}")

        print(f"Found {len(pitcher_opponents)} starting pitchers for today (probable and confirmed).")
        return pitcher_opponents

    except Exception as e:
        print(f"Error fetching schedule: {e}")
        return []


def run_projection_for_pitcher(pitcher_id, opponent, pitcher_name=''):
    """Run the full projection analysis for a given pitcher ID and opponent."""
    display_name = pitcher_name if pitcher_name and not pitcher_name.startswith('ID:') else str(pitcher_id)
    print(f"\n{'='*50}")
    print(f"Analyzing {display_name} (ID: {pitcher_id}) vs {opponent}")
    print(f"{'='*50}")

    try:
        # =========================
        # STEP 1: PULL DATA (MLB Stats API game log)
        # =========================
        now_et = get_current_et()
        cutoff = now_et - timedelta(days=14)
        season = now_et.year

        gamelog_url = (
            f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
            f"?stats=gameLog&group=pitching&season={season}&sportId=1"
        )
        gl_resp = requests.get(gamelog_url, headers=REQUEST_HEADERS, timeout=20)
        gl_resp.raise_for_status()
        gl_data = gl_resp.json()

        splits = []
        for stat_block in gl_data.get('stats', []):
            for split in stat_block.get('splits', []):
                game_date = pd.to_datetime(split.get('date', ''))
                if game_date < pd.Timestamp(cutoff.date()):
                    continue
                s = split.get('stat', {})
                ip_str = s.get('inningsPitched', '0')
                # Convert "6.2" -> 6 + 2/3 innings
                ip_parts = str(ip_str).split('.')
                ip_val = int(ip_parts[0]) + (int(ip_parts[1]) / 3 if len(ip_parts) > 1 and ip_parts[1] else 0)
                splits.append({
                    'game_date': game_date,
                    'strikeouts': int(s.get('strikeOuts', 0)),
                    'innings_pitched': ip_val,
                    'pitch_count': int(s.get('pitchesThrown', 0)),
                })

        if not splits:
            print(f"No recent game log data found for pitcher {pitcher_id}. Skipping.")
            return

        game_stats = pd.DataFrame(splits)
        print("Opponent:", opponent)
        print(f"Games found in last 14 days: {len(game_stats)}")

        # Pull current season stats with a daily ET cache refresh
        team_stats_entry = get_opponent_stats(opponent)
        if team_stats_entry is None:
            print(f"Skipping projection for pitcher {pitcher_id} vs {opponent} - no opponent stats available")
            return

        opp_k_rate = team_stats_entry['K%']
        opp_woba = team_stats_entry['wOBA']
        opp_ops = team_stats_entry['OPS']
        print(f"Opponent Stats for {opponent}: K%={opp_k_rate:.1%}, wOBA={opp_woba:.3f}, OPS={opp_ops:.3f}")
        print_cache_status(opponent, team_stats_entry)

        # =========================
        # STEP 2: AVERAGES (LAST 14 DAYS)
        # =========================
        avg_k = game_stats['strikeouts'].mean()
        avg_ip = game_stats['innings_pitched'].mean()
        avg_pitches = game_stats['pitch_count'].mean()

        # Season-average fastball velocity from Baseball Savant arsenal stats
        avg_velo = None
        try:
            velo_url = (
                f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
                f"?type=pitcher&pitchType=FF&year={season}&filteredResults=results&min=0"
            )
            velo_resp = requests.get(velo_url, headers=REQUEST_HEADERS, timeout=15)
            velo_resp.raise_for_status()
            velo_data = velo_resp.json()
            for row in velo_data:
                if str(row.get('pitcher_id', '')) == str(pitcher_id):
                    avg_velo = row.get('avg_speed')
                    break
        except Exception as e:
            print(f"Could not fetch velocity for {pitcher_id}: {e}")

        # =========================
        # STEP 5: MATCHUP-ADJUSTED PREDICTION
        # =========================

        # Pitcher baseline
        total_k = game_stats['strikeouts'].sum()
        total_ip = game_stats['innings_pitched'].sum()

        if total_ip <= 0:
            print(f"No innings recorded for {display_name} in the last 14 days. Skipping.")
            return

        k_per_inning = total_k / total_ip
        expected_ip = avg_ip

        # --- League averages (approx constants) ---
        league_k_rate = 0.22
        league_woba = 0.320
        league_ops = 0.730

        # --- Adjustments ---
        k_factor = opp_k_rate / league_k_rate

        # Contact penalty (better hitters reduce Ks)
        contact_factor = ((opp_woba / league_woba) + (opp_ops / league_ops)) / 2

        # Final adjustment
        adjustment = k_factor / contact_factor

        # Final expected Ks
        expected_k = expected_ip * k_per_inning * adjustment

        # =========================
        # STEP 6: PROBABILITIES
        # =========================
        prob_5_plus = 1 - stats.poisson.cdf(4, expected_k)
        prob_6_plus = 1 - stats.poisson.cdf(5, expected_k)
        prob_7_plus = 1 - stats.poisson.cdf(6, expected_k)

        # =========================
        # OUTPUT
        # =========================

        print("=== Last 14 Days ===")
        print("Avg Strikeouts:", round(avg_k, 2))
        print("Avg Innings:", round(avg_ip, 2))
        print("Avg Pitch Count:", round(avg_pitches, 1))
        print("Avg Velocity:", round(avg_velo, 1) if avg_velo else "N/A")

        print("\n=== Projection ===")
        print("Expected Strikeouts:", round(expected_k, 2))

        print("\n=== Probabilities ===")
        print("K >= 5:", round(prob_5_plus, 3))
        print("K >= 6:", round(prob_6_plus, 3))
        print("K >= 7:", round(prob_7_plus, 3))

        return {
            "avg_k":       round(avg_k, 2),
            "avg_ip":      round(avg_ip, 2),
            "avg_pitches": round(avg_pitches, 1),
            "avg_velo":    round(avg_velo, 1) if avg_velo else None,
            "expected_k":  round(expected_k, 2),
            "prob_5":      round(prob_5_plus, 3),
            "prob_6":      round(prob_6_plus, 3),
            "prob_7":      round(prob_7_plus, 3),
            "opp_k_pct":   opp_k_rate,
            "opp_woba":    opp_woba,
            "opp_ops":     opp_ops,
        }

    except Exception as e:
        print(f"Error analyzing pitcher {pitcher_id}: {e}")
        return {"error": str(e)}


# =========================
# MAIN EXECUTION
# =========================
if __name__ == "__main__":
    pitcher_opponents = get_starting_pitchers_today()
    if not pitcher_opponents:
        print("No starting pitchers found for today. Exiting.")
        exit()

    for pitcher_id, opponent, pitcher_name in pitcher_opponents:
        run_projection_for_pitcher(pitcher_id, opponent, pitcher_name)
