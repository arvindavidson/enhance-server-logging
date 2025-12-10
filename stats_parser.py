#!/usr/bin/env python3
"""
Reforger Server Log Stats Parser
Analyzes JSON logs from the Enhanced Server Logging mod
Author: Generated for special mission analysis
"""

import json
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Any
import argparse


class PlayerStats:
    """Stores statistics for a single player"""
    def __init__(self, player_id: str, name: str, bohemia_id: str):
        self.player_id = player_id
        self.name = name
        self.bohemia_id = bohemia_id
        self.kills = 0
        self.deaths = 0
        self.teamkills = 0
        self.weapons_used = defaultdict(int)
        self.killed_by_weapons = defaultdict(int)
        self.connections = 0
        self.disconnections = 0
        self.spawns = 0
        self.factions = set()
        self.connect_times = []
        self.disconnect_times = []

        # Advanced stats for achievements
        self.kill_timestamps = []  # Track when kills happened
        self.death_timestamps = []  # Track when deaths happened
        self.victims = defaultdict(int)  # Who this player killed (name -> count)
        self.killers = defaultdict(int)  # Who killed this player (name -> count)
        self.kill_streaks = []  # List of kill streak lengths
        self.current_streak = 0
        self.longest_streak = 0
        self.first_blood = False  # Did they get first kill?

    def add_kill(self, weapon: str = "Unknown", is_teamkill: bool = False, victim_name: str = "", timestamp: int = 0):
        """Record a kill"""
        self.kills += 1
        if is_teamkill:
            self.teamkills += 1
        if weapon:
            self.weapons_used[weapon] += 1
        if victim_name:
            self.victims[victim_name] += 1
        if timestamp:
            self.kill_timestamps.append(timestamp)

        # Update kill streak
        self.current_streak += 1
        if self.current_streak > self.longest_streak:
            self.longest_streak = self.current_streak

    def add_death(self, weapon: str = "Unknown", killer_name: str = "", timestamp: int = 0):
        """Record a death"""
        self.deaths += 1
        if weapon:
            self.killed_by_weapons[weapon] += 1
        if killer_name:
            self.killers[killer_name] += 1
        if timestamp:
            self.death_timestamps.append(timestamp)

        # Reset kill streak and record it
        if self.current_streak > 0:
            self.kill_streaks.append(self.current_streak)
            self.current_streak = 0

    @property
    def kd_ratio(self) -> float:
        """Calculate K/D ratio"""
        if self.deaths == 0:
            return float(self.kills)
        return round(self.kills / self.deaths, 2)

    @property
    def total_playtime(self) -> int:
        """Calculate total playtime in seconds"""
        playtime = 0
        for i in range(min(len(self.connect_times), len(self.disconnect_times))):
            playtime += (self.disconnect_times[i] - self.connect_times[i])
        return int(playtime)

    @property
    def favorite_weapon(self) -> str:
        """Get most used weapon"""
        if not self.weapons_used:
            return "N/A"
        return max(self.weapons_used.items(), key=lambda x: x[1])[0]

    @property
    def weapon_diversity(self) -> int:
        """Number of different weapons used"""
        return len(self.weapons_used)

    @property
    def nemesis(self) -> str:
        """Player who killed them most"""
        if not self.killers:
            return "None"
        return max(self.killers.items(), key=lambda x: x[1])[0]

    @property
    def favorite_victim(self) -> str:
        """Player they killed most"""
        if not self.victims:
            return "None"
        return max(self.victims.items(), key=lambda x: x[1])[0]

    @property
    def kills_per_minute(self) -> float:
        """Calculate kills per minute"""
        if self.total_playtime == 0:
            return 0.0
        return round((self.kills / self.total_playtime) * 60, 2)

    @property
    def average_life_duration(self) -> float:
        """Average time between deaths in seconds"""
        if len(self.death_timestamps) <= 1:
            return 0.0

        life_durations = []
        for i in range(1, len(self.death_timestamps)):
            life_durations.append(self.death_timestamps[i] - self.death_timestamps[i-1])

        return round(sum(life_durations) / len(life_durations), 1) if life_durations else 0.0


class MissionStats:
    """Stores statistics for the entire mission"""
    def __init__(self, pve_mode: bool = False):
        self.pve_mode = pve_mode
        self.start_time = None
        self.end_time = None
        self.end_reason = None
        self.total_kills = 0
        self.total_deaths = 0
        self.total_teamkills = 0
        self.total_ai_deaths = 0  # Deaths from AI (PvE mode)
        self.total_suicides = 0
        self.total_other_deaths = 0
        self.players = {}  # player_id -> PlayerStats
        self.player_name_map = {}  # name -> player_id (for deduplication)
        self.weapon_stats = defaultdict(int)
        self.ai_weapon_stats = defaultdict(int)  # Weapons used by AI (PvE mode)
        self.faction_stats = defaultdict(lambda: {"kills": 0, "deaths": 0})
        self.first_kill_recorded = False  # Track if first blood has been awarded

    def get_or_create_player(self, player_id: str, name: str, bohemia_id: str = "") -> PlayerStats:
        """Get existing player or create new one"""
        # Try to find by Bohemia ID first (most reliable)
        if bohemia_id:
            for pid, player in self.players.items():
                if player.bohemia_id == bohemia_id:
                    return player

        # Try to find by name
        if name in self.player_name_map:
            return self.players[self.player_name_map[name]]

        # Create new player
        if player_id not in self.players:
            self.players[player_id] = PlayerStats(player_id, name, bohemia_id)
            self.player_name_map[name] = player_id

        return self.players[player_id]

    @property
    def duration(self) -> int:
        """Mission duration in seconds"""
        if self.start_time and self.end_time:
            return int(self.end_time - self.start_time)
        return 0

    @property
    def total_players(self) -> int:
        """Total unique players"""
        return len(self.players)


class LogParser:
    """Parses Reforger server logs"""

    def __init__(self, log_directory: Path, pve_mode: bool = False):
        self.log_directory = Path(log_directory)
        self.pve_mode = pve_mode
        self.stats = MissionStats(pve_mode=pve_mode)

    def parse_log_line(self, line: str) -> Dict[str, Any]:
        """Parse a single log line"""
        # Log format: "TIMESTAMP {JSON}"
        # Example: "2025-12-10 14:23:45 {"function":"PlayerKilled",...}"

        # Match timestamp and JSON
        match = re.match(r'^([\d\-:\s]+)\s+(\{.+\})$', line.strip())
        if not match:
            return None

        timestamp_str, json_str = match.groups()

        try:
            log_data = json.loads(json_str)
            log_data['_timestamp'] = timestamp_str
            return log_data
        except json.JSONDecodeError:
            return None

    def parse_file(self, file_path: Path):
        """Parse a single log file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                log_entry = self.parse_log_line(line)
                if log_entry:
                    self.process_log_entry(log_entry)

    def process_log_entry(self, entry: Dict[str, Any]):
        """Process a single log entry"""
        function = entry.get('function', '')

        if function == 'OnGameStart':
            self.process_game_start(entry)
        elif function == 'GameEnd':
            self.process_game_end(entry)
        elif function == 'GameModeEnd':
            self.process_game_mode_end(entry)
        elif function == 'PlayerKilled':
            self.process_player_killed(entry)
        elif function == 'PlayerConnected':
            self.process_player_connected(entry)
        elif function == 'PlayerRegistered':
            self.process_player_registered(entry)
        elif function == 'PlayerDisconnected':
            self.process_player_disconnected(entry)
        elif function == 'PlayerSpawned':
            self.process_player_spawned(entry)

    def process_game_start(self, entry: Dict[str, Any]):
        """Process game start event"""
        if self.stats.start_time is None:
            timestamp = entry.get('systemTimeInt')
            if timestamp:
                self.stats.start_time = int(timestamp)

    def process_game_end(self, entry: Dict[str, Any]):
        """Process game end event"""
        timestamp = entry.get('systemTimeInt')
        if timestamp:
            self.stats.end_time = int(timestamp)

    def process_game_mode_end(self, entry: Dict[str, Any]):
        """Process game mode end event"""
        end_reason_code = entry.get('GetEndReason', '')

        # Map end reason codes to descriptions
        end_reasons = {
            '-1': 'UNDEFINED',
            '-2': 'TIME LIMIT',
            '-3': 'SCORE LIMIT',
            '-4': 'DRAW',
            '-5': 'SERVER RESTART'
        }

        self.stats.end_reason = end_reasons.get(end_reason_code, f'Unknown ({end_reason_code})')

        timestamp = entry.get('systemTimeInt')
        if timestamp and not self.stats.end_time:
            self.stats.end_time = int(timestamp)

    def process_player_killed(self, entry: Dict[str, Any]):
        """Process player killed event"""
        # Victim information
        victim_id = entry.get('VictimPlayerID', '')
        victim_name = entry.get('VictimPlayerName', 'Unknown')
        victim_bohemia_id = entry.get('VictimPlayerBiId', '')
        victim_faction = entry.get('VictimPlayerFaction', '')

        # Killer information
        killer_id = entry.get('KillerPlayerID', '')
        killer_name = entry.get('KillerPlayerName', '')
        killer_bohemia_id = entry.get('KillerPlayerBiId', '')
        killer_faction = entry.get('KillerPlayerFaction', '')

        # Weapon and death type info
        weapon = entry.get('KillerPlayerWeaponName', 'Unknown Weapon')
        is_teamkill = entry.get('IsTeamKill', '0') == '1' or entry.get('IsTeamKill', 'false').lower() == 'true'
        relation = entry.get('Relation', '')

        # Get timestamp
        timestamp = entry.get('systemTimeInt', 0)
        if timestamp:
            timestamp = int(timestamp)

        # Determine death type
        is_ai_kill = (killer_id == '0' and relation == 'KILLED_BY_ENEMY_AI')
        is_suicide = (relation == 'SUICIDE')
        is_other_death = (relation == 'OTHER_DEATH')

        # Update victim stats (always update victim)
        if victim_id and victim_name:
            victim = self.stats.get_or_create_player(victim_id, victim_name, victim_bohemia_id)
            victim.add_death(weapon, killer_name if killer_name else "AI" if is_ai_kill else "Environment", timestamp)
            if victim_faction:
                victim.factions.add(victim_faction)
            self.stats.faction_stats[victim_faction]["deaths"] += 1

        # Update killer stats (only if it's a real player kill, not AI)
        if not is_ai_kill and killer_id and killer_id != '0' and killer_name:
            killer = self.stats.get_or_create_player(killer_id, killer_name, killer_bohemia_id)
            killer.add_kill(weapon, is_teamkill, victim_name, timestamp)
            if killer_faction:
                killer.factions.add(killer_faction)

            # Only count faction kills for player kills, not suicides
            if not is_suicide:
                self.stats.faction_stats[killer_faction]["kills"] += 1

            # Award First Blood (only for non-teamkill, non-suicide)
            if not self.stats.first_kill_recorded and not is_teamkill and not is_suicide:
                killer.first_blood = True
                self.stats.first_kill_recorded = True

        # Update global stats
        self.stats.total_deaths += 1

        if is_ai_kill:
            self.stats.total_ai_deaths += 1
            self.stats.ai_weapon_stats[weapon] += 1
        elif is_suicide:
            self.stats.total_suicides += 1
        elif is_other_death:
            self.stats.total_other_deaths += 1
        elif is_teamkill:
            self.stats.total_teamkills += 1
            self.stats.total_kills += 1  # Teamkill is still a player kill
        else:
            self.stats.total_kills += 1  # Other player kills

        # Track weapon stats
        if is_ai_kill:
            # In PvE mode, track AI weapons separately
            pass  # Already tracked in ai_weapon_stats above
        else:
            self.stats.weapon_stats[weapon] += 1

    def process_player_connected(self, entry: Dict[str, Any]):
        """Process player connected event"""
        player_id = entry.get('playerId', '')
        player_name = entry.get('playerName', 'Unknown')
        bohemia_id = entry.get('playerBiId', '')

        if player_id and player_name:
            player = self.stats.get_or_create_player(player_id, player_name, bohemia_id)
            player.connections += 1

            timestamp = entry.get('systemTimeInt')
            if timestamp:
                player.connect_times.append(int(timestamp))

    def process_player_registered(self, entry: Dict[str, Any]):
        """Process player registered event"""
        player_id = entry.get('playerId', '')
        player_name = entry.get('playerName', 'Unknown')
        bohemia_id = entry.get('playerBiId', '')

        if player_id and player_name:
            player = self.stats.get_or_create_player(player_id, player_name, bohemia_id)
            # Store Bohemia ID if we didn't have it before
            if bohemia_id and not player.bohemia_id:
                player.bohemia_id = bohemia_id

    def process_player_disconnected(self, entry: Dict[str, Any]):
        """Process player disconnected event"""
        player_id = entry.get('playerId', '')
        player_name = entry.get('playerName', 'Unknown')
        bohemia_id = entry.get('playerBiId', '')

        if player_id and player_name:
            player = self.stats.get_or_create_player(player_id, player_name, bohemia_id)
            player.disconnections += 1

            timestamp = entry.get('systemTimeInt')
            if timestamp:
                player.disconnect_times.append(int(timestamp))

    def process_player_spawned(self, entry: Dict[str, Any]):
        """Process player spawned event"""
        player_id = entry.get('playerId', '')
        player_name = entry.get('playerName', 'Unknown')
        bohemia_id = entry.get('playerBiId', '')
        faction = entry.get('playerFaction', '')

        if player_id and player_name:
            player = self.stats.get_or_create_player(player_id, player_name, bohemia_id)
            player.spawns += 1
            if faction:
                player.factions.add(faction)

    def parse_directory(self, recursive: bool = False):
        """Parse all JSON files in directory"""
        pattern = '**/*.json' if recursive else '*.json'

        json_files = sorted(self.log_directory.glob(pattern))

        if not json_files:
            print(f"No JSON log files found in {self.log_directory}")
            return

        print(f"Found {len(json_files)} JSON log file(s)")
        for json_file in json_files:
            print(f"  Parsing: {json_file.name}")
            self.parse_file(json_file)

    def calculate_achievements(self) -> Dict[str, Any]:
        """Calculate special achievements and awards (PvE optimized)"""
        achievements = {
            'first_blood': None,
            'kill_streak_king': None,
            'sharp_shooter': None,
            'survivor': None,
            'rampage': None,
            'weapon_master': None,
            'versatile': None,
            'dead_eye': None,
            'glass_cannon': None,
            'untouchable': None,
            'team_player': None
        }

        if not self.stats.players:
            return achievements

        players = list(self.stats.players.values())

        # First Blood - player with first kill
        for player in players:
            if player.first_blood:
                achievements['first_blood'] = player.name
                break

        # Kill Streak King - longest kill streak
        max_streak = max((p.longest_streak for p in players), default=0)
        if max_streak > 0:
            for player in players:
                if player.longest_streak == max_streak:
                    achievements['kill_streak_king'] = (player.name, max_streak)
                    break

        # Sharp Shooter - highest K/D ratio (min 10 kills)
        eligible_players = [p for p in players if p.kills >= 10]
        if eligible_players:
            sharpshooter = max(eligible_players, key=lambda p: p.kd_ratio)
            achievements['sharp_shooter'] = (sharpshooter.name, sharpshooter.kd_ratio)

        # Survivor - fewest deaths (min 10 kills)
        if eligible_players:
            survivor = min(eligible_players, key=lambda p: p.deaths)
            achievements['survivor'] = (survivor.name, survivor.deaths)

        # Rampage - most kills
        if players:
            rampage = max(players, key=lambda p: p.kills)
            if rampage.kills > 0:
                achievements['rampage'] = (rampage.name, rampage.kills)

        # Weapon Master - most weapon diversity
        if players:
            master = max(players, key=lambda p: p.weapon_diversity)
            if master.weapon_diversity > 1:
                achievements['weapon_master'] = (master.name, master.weapon_diversity)

        # Versatile - highest weapon diversity with min kills
        versatile_players = [p for p in players if p.kills >= 10]
        if versatile_players:
            versatile = max(versatile_players, key=lambda p: p.weapon_diversity)
            achievements['versatile'] = (versatile.name, versatile.weapon_diversity)

        # Dead Eye - highest kills per minute (min 15 minutes playtime)
        active_players = [p for p in players if p.total_playtime >= 900]  # 15 minutes
        if active_players:
            deadeye = max(active_players, key=lambda p: p.kills_per_minute)
            if deadeye.kills_per_minute > 0:
                achievements['dead_eye'] = (deadeye.name, deadeye.kills_per_minute)

        # Glass Cannon - high kills but also high deaths
        eligible_glass = [p for p in players if p.kills >= 15 and p.deaths >= 15]
        if eligible_glass:
            glass = max(eligible_glass, key=lambda p: p.kills + p.deaths)
            achievements['glass_cannon'] = (glass.name, glass.kills, glass.deaths)

        # Untouchable - longest average life (min 5 deaths)
        long_life_players = [p for p in players if len(p.death_timestamps) >= 5]
        if long_life_players:
            untouchable = max(long_life_players, key=lambda p: p.average_life_duration)
            if untouchable.average_life_duration > 0:
                achievements['untouchable'] = (untouchable.name, untouchable.average_life_duration)

        # Team Player - zero teamkills with min 10 kills
        team_players = [p for p in players if p.kills >= 10 and p.teamkills == 0]
        if team_players:
            best_team = max(team_players, key=lambda p: p.kills)
            achievements['team_player'] = (best_team.name, best_team.kills)

        # Note: Lone Wolf achievement removed - not applicable in PvE scenarios

        return achievements

    def generate_text_report(self) -> str:
        """Generate a text summary report"""
        lines = []
        lines.append("=" * 80)
        if self.pve_mode:
            lines.append("REFORGER PvE MISSION STATISTICS REPORT")
        else:
            lines.append("REFORGER MISSION STATISTICS REPORT")
        lines.append("=" * 80)
        lines.append("")

        # Mission overview
        lines.append("MISSION OVERVIEW")
        lines.append("-" * 80)
        if self.stats.start_time:
            lines.append(f"Start Time: {datetime.fromtimestamp(self.stats.start_time).strftime('%Y-%m-%d %H:%M:%S')}")
        if self.stats.end_time:
            lines.append(f"End Time:   {datetime.fromtimestamp(self.stats.end_time).strftime('%Y-%m-%d %H:%M:%S')}")
        if self.stats.duration:
            hours = self.stats.duration // 3600
            minutes = (self.stats.duration % 3600) // 60
            seconds = self.stats.duration % 60
            lines.append(f"Duration:   {hours}h {minutes}m {seconds}s")
        if self.stats.end_reason:
            lines.append(f"End Reason: {self.stats.end_reason}")
        lines.append(f"Total Players: {self.stats.total_players}")

        if self.pve_mode:
            lines.append(f"Total Deaths: {self.stats.total_deaths}")
            lines.append(f"  - Deaths from AI: {self.stats.total_ai_deaths}")
            lines.append(f"  - Suicides: {self.stats.total_suicides}")
            lines.append(f"  - Teamkills: {self.stats.total_teamkills}")
            lines.append(f"  - Other: {self.stats.total_other_deaths}")
        else:
            lines.append(f"Total Kills:   {self.stats.total_kills}")
            lines.append(f"Total Teamkills: {self.stats.total_teamkills}")
        lines.append("")

        # Faction stats
        if self.stats.faction_stats:
            lines.append("FACTION STATISTICS")
            lines.append("-" * 80)
            for faction, stats in sorted(self.stats.faction_stats.items()):
                if faction:
                    if self.pve_mode:
                        lines.append(f"{faction}: {stats['deaths']} deaths")
                    else:
                        lines.append(f"{faction}: {stats['kills']} kills, {stats['deaths']} deaths")
            lines.append("")

        # Weapon stats
        if self.pve_mode and self.stats.ai_weapon_stats:
            lines.append("TOP AI WEAPONS (weapons that killed players)")
            lines.append("-" * 80)
            top_weapons = sorted(self.stats.ai_weapon_stats.items(), key=lambda x: x[1], reverse=True)[:10]
            for i, (weapon, kills) in enumerate(top_weapons, 1):
                lines.append(f"{i:2}. {weapon}: {kills} kills")
            lines.append("")
        elif self.stats.weapon_stats:
            lines.append("TOP WEAPONS (by kills)")
            lines.append("-" * 80)
            top_weapons = sorted(self.stats.weapon_stats.items(), key=lambda x: x[1], reverse=True)[:10]
            for i, (weapon, kills) in enumerate(top_weapons, 1):
                lines.append(f"{i:2}. {weapon}: {kills} kills")
            lines.append("")

        # Player leaderboard
        if self.stats.players:
            if self.pve_mode:
                lines.append("PLAYER SURVIVAL LEADERBOARD (by fewest deaths)")
                lines.append("-" * 80)
                sorted_players = sorted(self.stats.players.values(), key=lambda p: p.deaths)[:20]
                lines.append(f"{'#':<4} {'Player Name':<25} {'Deaths':<8} {'Spawns':<8} {'TKs':<6}")
                lines.append("-" * 80)
                for i, player in enumerate(sorted_players, 1):
                    lines.append(
                        f"{i:<4} {player.name[:24]:<25} {player.deaths:<8} {player.spawns:<8} "
                        f"{player.teamkills:<6}"
                    )
            else:
                lines.append("PLAYER LEADERBOARD (by kills)")
                lines.append("-" * 80)
                sorted_players = sorted(self.stats.players.values(), key=lambda p: p.kills, reverse=True)[:20]
                lines.append(f"{'#':<4} {'Player Name':<25} {'Kills':<8} {'Deaths':<8} {'K/D':<8} {'TKs':<6}")
                lines.append("-" * 80)
                for i, player in enumerate(sorted_players, 1):
                    lines.append(
                        f"{i:<4} {player.name[:24]:<25} {player.kills:<8} {player.deaths:<8} "
                        f"{player.kd_ratio:<8} {player.teamkills:<6}"
                    )

        return "\n".join(lines)

    def export_player_stats_csv(self, output_file: Path):
        """Export player statistics to CSV"""
        with open(output_file, 'w', encoding='utf-8') as f:
            # Header
            f.write("Rank,Player Name,Bohemia ID,Kills,Deaths,K/D Ratio,Teamkills,Spawns,")
            f.write("Connections,Disconnections,Playtime (seconds),Factions,Favorite Weapon\n")

            # Sort by kills
            sorted_players = sorted(self.stats.players.values(), key=lambda p: p.kills, reverse=True)

            for i, player in enumerate(sorted_players, 1):
                # Get favorite weapon
                fav_weapon = "N/A"
                if player.weapons_used:
                    fav_weapon = max(player.weapons_used.items(), key=lambda x: x[1])[0]

                factions = "|".join(player.factions) if player.factions else "Unknown"

                f.write(f"{i},{player.name},{player.bohemia_id},{player.kills},{player.deaths},")
                f.write(f"{player.kd_ratio},{player.teamkills},{player.spawns},")
                f.write(f"{player.connections},{player.disconnections},{player.total_playtime},")
                f.write(f'"{factions}","{fav_weapon}"\n')

        print(f"Player stats exported to: {output_file}")

    def export_weapon_stats_csv(self, output_file: Path):
        """Export weapon statistics to CSV"""
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("Rank,Weapon Name,Total Kills\n")

            sorted_weapons = sorted(self.stats.weapon_stats.items(), key=lambda x: x[1], reverse=True)

            for i, (weapon, kills) in enumerate(sorted_weapons, 1):
                f.write(f'{i},"{weapon}",{kills}\n')

        print(f"Weapon stats exported to: {output_file}")

    def generate_html_report(self, output_file: Path):
        """Generate an HTML report with interactive tables"""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reforger Mission Statistics Report</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: #333;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }
        .header p {
            font-size: 1.1em;
            opacity: 0.9;
        }
        .content {
            padding: 40px;
        }
        .section {
            margin-bottom: 50px;
        }
        .section h2 {
            color: #667eea;
            font-size: 1.8em;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #667eea;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 25px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.2s;
        }
        .stat-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }
        .stat-card h3 {
            color: #667eea;
            font-size: 0.9em;
            text-transform: uppercase;
            margin-bottom: 10px;
            letter-spacing: 1px;
        }
        .stat-card .value {
            font-size: 2.5em;
            font-weight: bold;
            color: #333;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        table thead {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        table th {
            padding: 15px;
            text-align: left;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 0.5px;
        }
        table tbody tr {
            border-bottom: 1px solid #e0e0e0;
            transition: background-color 0.2s;
        }
        table tbody tr:hover {
            background-color: #f5f7fa;
        }
        table tbody tr:nth-child(even) {
            background-color: #fafafa;
        }
        table tbody tr:nth-child(even):hover {
            background-color: #f0f0f0;
        }
        table td {
            padding: 12px 15px;
        }
        .rank-badge {
            display: inline-block;
            width: 30px;
            height: 30px;
            line-height: 30px;
            text-align: center;
            border-radius: 50%;
            font-weight: bold;
            color: white;
        }
        .rank-1 { background: linear-gradient(135deg, #FFD700, #FFA500); }
        .rank-2 { background: linear-gradient(135deg, #C0C0C0, #808080); }
        .rank-3 { background: linear-gradient(135deg, #CD7F32, #8B4513); }
        .rank-other { background: linear-gradient(135deg, #667eea, #764ba2); }
        .kd-positive { color: #2ecc71; font-weight: bold; }
        .kd-negative { color: #e74c3c; font-weight: bold; }
        .kd-neutral { color: #f39c12; font-weight: bold; }
        .footer {
            background: #f5f7fa;
            padding: 20px;
            text-align: center;
            color: #666;
            font-size: 0.9em;
        }
        .search-box {
            margin-bottom: 20px;
            padding: 12px;
            width: 100%;
            max-width: 400px;
            border: 2px solid #667eea;
            border-radius: 5px;
            font-size: 1em;
        }
        .achievements-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }
        .achievement-card {
            background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            border-left: 5px solid #f39c12;
            transition: transform 0.2s;
        }
        .achievement-card:hover {
            transform: translateY(-3px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }
        .achievement-icon {
            font-size: 2em;
            margin-bottom: 10px;
        }
        .achievement-title {
            font-size: 1.1em;
            font-weight: bold;
            color: #d35400;
            margin-bottom: 5px;
        }
        .achievement-player {
            font-size: 1.3em;
            font-weight: bold;
            color: #2c3e50;
            margin: 5px 0;
        }
        .achievement-value {
            font-size: 0.9em;
            color: #7f8c8d;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎮 Reforger Mission Statistics</h1>
            <p>Enhanced Server Logging Analysis Report</p>
        </div>

        <div class="content">
"""

        # Mission Overview Section
        html += '            <div class="section">\n'
        html += '                <h2>📊 Mission Overview</h2>\n'
        html += '                <div class="stats-grid">\n'

        # Duration card
        if self.stats.duration:
            hours = self.stats.duration // 3600
            minutes = (self.stats.duration % 3600) // 60
            duration_str = f"{hours}h {minutes}m"
        else:
            duration_str = "N/A"

        if self.pve_mode:
            stats_cards = [
                ("Duration", duration_str),
                ("Total Players", str(self.stats.total_players)),
                ("Total Deaths", str(self.stats.total_deaths)),
                ("AI Kills", str(self.stats.total_ai_deaths)),
                ("Suicides", str(self.stats.total_suicides)),
                ("Teamkills", str(self.stats.total_teamkills)),
            ]
        else:
            stats_cards = [
                ("Duration", duration_str),
                ("Total Players", str(self.stats.total_players)),
                ("Total Kills", str(self.stats.total_kills)),
                ("Teamkills", str(self.stats.total_teamkills)),
            ]

        if self.stats.end_reason:
            stats_cards.append(("End Reason", self.stats.end_reason))

        for title, value in stats_cards:
            html += f'                    <div class="stat-card">\n'
            html += f'                        <h3>{title}</h3>\n'
            html += f'                        <div class="value">{value}</div>\n'
            html += f'                    </div>\n'

        html += '                </div>\n'

        # Mission times
        if self.stats.start_time or self.stats.end_time:
            html += '                <div class="stats-grid">\n'
            if self.stats.start_time:
                start_str = datetime.fromtimestamp(self.stats.start_time).strftime('%Y-%m-%d %H:%M:%S')
                html += f'                    <div class="stat-card">\n'
                html += f'                        <h3>Start Time</h3>\n'
                html += f'                        <div class="value" style="font-size: 1.3em;">{start_str}</div>\n'
                html += f'                    </div>\n'
            if self.stats.end_time:
                end_str = datetime.fromtimestamp(self.stats.end_time).strftime('%Y-%m-%d %H:%M:%S')
                html += f'                    <div class="stat-card">\n'
                html += f'                        <h3>End Time</h3>\n'
                html += f'                        <div class="value" style="font-size: 1.3em;">{end_str}</div>\n'
                html += f'                    </div>\n'
            html += '                </div>\n'

        html += '            </div>\n'

        # Player Leaderboard
        html += '            <div class="section">\n'
        if self.pve_mode:
            html += '                <h2>🏆 Player Survival Leaderboard</h2>\n'
        else:
            html += '                <h2>🏆 Player Leaderboard</h2>\n'
        html += '                <input type="text" class="search-box" id="playerSearch" placeholder="Search players..." onkeyup="filterTable(\'playerTable\', \'playerSearch\')">\n'
        html += '                <table id="playerTable">\n'
        html += '                    <thead>\n'
        html += '                        <tr>\n'
        html += '                            <th>Rank</th>\n'
        html += '                            <th>Player Name</th>\n'
        if self.pve_mode:
            html += '                            <th>Deaths</th>\n'
            html += '                            <th>Spawns</th>\n'
            html += '                            <th>Teamkills</th>\n'
        else:
            html += '                            <th>Kills</th>\n'
            html += '                            <th>Deaths</th>\n'
            html += '                            <th>K/D</th>\n'
            html += '                            <th>Streak</th>\n'
            html += '                            <th>Fav Weapon</th>\n'
            html += '                            <th>Teamkills</th>\n'
        html += '                        </tr>\n'
        html += '                    </thead>\n'
        html += '                    <tbody>\n'

        if self.pve_mode:
            sorted_players = sorted(self.stats.players.values(), key=lambda p: p.deaths)
        else:
            sorted_players = sorted(self.stats.players.values(), key=lambda p: p.kills, reverse=True)

        for i, player in enumerate(sorted_players, 1):
            rank_class = f"rank-{i}" if i <= 3 else "rank-other"

            html += f'                        <tr>\n'
            html += f'                            <td><span class="rank-badge {rank_class}">{i}</span></td>\n'
            html += f'                            <td><strong>{player.name}</strong></td>\n'

            if self.pve_mode:
                html += f'                            <td>{player.deaths}</td>\n'
                html += f'                            <td>{player.spawns}</td>\n'
                html += f'                            <td>{player.teamkills}</td>\n'
            else:
                kd_class = "kd-positive" if player.kd_ratio > 1 else ("kd-negative" if player.kd_ratio < 1 else "kd-neutral")
                fav_weapon = player.favorite_weapon
                if len(fav_weapon) > 15:
                    fav_weapon = fav_weapon[:12] + "..."

                html += f'                            <td>{player.kills}</td>\n'
                html += f'                            <td>{player.deaths}</td>\n'
                html += f'                            <td class="{kd_class}">{player.kd_ratio}</td>\n'
                html += f'                            <td>{player.longest_streak}</td>\n'
                html += f'                            <td>{fav_weapon}</td>\n'
                html += f'                            <td>{player.teamkills}</td>\n'

            html += f'                        </tr>\n'

        html += '                    </tbody>\n'
        html += '                </table>\n'
        html += '            </div>\n'

        # Achievements Section
        achievements = self.calculate_achievements()
        html += '            <div class="section">\n'
        html += '                <h2>🏅 Achievements & Special Awards</h2>\n'
        html += '                <div class="achievements-grid">\n'

        # Achievement mapping with icons and descriptions (PvE focused)
        achievement_map = {
            'first_blood': ('🩸', 'First Blood', 'Got the first kill of the match'),
            'kill_streak_king': ('🔥', 'Kill Streak King', 'Longest kill streak: {}'),
            'sharp_shooter': ('🎯', 'Sharp Shooter', 'Highest K/D ratio: {}'),
            'survivor': ('🛡️', 'Survivor', 'Fewest deaths: {}'),
            'rampage': ('💀', 'Rampage', 'Most enemy kills: {}'),
            'weapon_master': ('🔫', 'Weapon Master', 'Used {} different weapons'),
            'versatile': ('🎲', 'Versatile', 'Most versatile: {} weapons'),
            'dead_eye': ('👁️', 'Dead Eye', '{} kills/min'),
            'glass_cannon': ('💥', 'Glass Cannon', '{} kills, {} deaths'),
            'untouchable': ('👻', 'Untouchable', 'Avg life: {}s'),
            'team_player': ('🤝', 'Team Player', 'Zero teamkills, {} kills')
        }

        for key, value in achievements.items():
            if value is not None:
                icon, title, desc_template = achievement_map.get(key, ('🏆', key.replace('_', ' ').title(), '{}'))

                if isinstance(value, tuple):
                    player_name = value[0]
                    if len(value) == 2:
                        description = desc_template.format(value[1])
                    elif len(value) == 3:
                        description = desc_template.format(value[1], value[2])
                    else:
                        description = desc_template
                else:
                    player_name = value
                    description = desc_template

                html += f'                    <div class="achievement-card">\n'
                html += f'                        <div class="achievement-icon">{icon}</div>\n'
                html += f'                        <div class="achievement-title">{title}</div>\n'
                html += f'                        <div class="achievement-player">{player_name}</div>\n'
                html += f'                        <div class="achievement-value">{description}</div>\n'
                html += f'                    </div>\n'

        html += '                </div>\n'
        html += '            </div>\n'

        # Weapon Statistics
        weapon_data_available = (self.pve_mode and self.stats.ai_weapon_stats) or (not self.pve_mode and self.stats.weapon_stats)
        if weapon_data_available:
            html += '            <div class="section">\n'
            if self.pve_mode:
                html += '                <h2>🔫 AI Weapon Statistics</h2>\n'
                html += '                <p style="text-align: center; color: #666; margin-bottom: 20px;">Weapons used by AI enemies to kill players</p>\n'
            else:
                html += '                <h2>🔫 Weapon Statistics</h2>\n'
            html += '                <table>\n'
            html += '                    <thead>\n'
            html += '                        <tr>\n'
            html += '                            <th>Rank</th>\n'
            html += '                            <th>Weapon Name</th>\n'
            html += '                            <th>Total Kills</th>\n'
            html += '                            <th>Kill Share</th>\n'
            html += '                        </tr>\n'
            html += '                    </thead>\n'
            html += '                    <tbody>\n'

            if self.pve_mode:
                sorted_weapons = sorted(self.stats.ai_weapon_stats.items(), key=lambda x: x[1], reverse=True)[:15]
                total_ai_kills = self.stats.total_ai_deaths
            else:
                sorted_weapons = sorted(self.stats.weapon_stats.items(), key=lambda x: x[1], reverse=True)[:15]
                total_ai_kills = self.stats.total_kills

            for i, (weapon, kills) in enumerate(sorted_weapons, 1):
                percentage = (kills / total_ai_kills * 100) if total_ai_kills > 0 else 0
                rank_class = f"rank-{i}" if i <= 3 else "rank-other"

                html += f'                        <tr>\n'
                html += f'                            <td><span class="rank-badge {rank_class}">{i}</span></td>\n'
                html += f'                            <td><strong>{weapon}</strong></td>\n'
                html += f'                            <td>{kills}</td>\n'
                html += f'                            <td>{percentage:.1f}%</td>\n'
                html += f'                        </tr>\n'

            html += '                    </tbody>\n'
            html += '                </table>\n'
            html += '            </div>\n'

        # Faction Statistics
        if self.stats.faction_stats:
            html += '            <div class="section">\n'
            html += '                <h2>⚔️ Faction Statistics</h2>\n'
            html += '                <table>\n'
            html += '                    <thead>\n'
            html += '                        <tr>\n'
            html += '                            <th>Faction</th>\n'
            html += '                            <th>Kills</th>\n'
            html += '                            <th>Deaths</th>\n'
            html += '                            <th>K/D Ratio</th>\n'
            html += '                        </tr>\n'
            html += '                    </thead>\n'
            html += '                    <tbody>\n'

            for faction, stats in sorted(self.stats.faction_stats.items(), key=lambda x: x[1]["kills"], reverse=True):
                if faction:
                    kd = stats["kills"] / stats["deaths"] if stats["deaths"] > 0 else float(stats["kills"])
                    kd_class = "kd-positive" if kd > 1 else ("kd-negative" if kd < 1 else "kd-neutral")

                    html += f'                        <tr>\n'
                    html += f'                            <td><strong>{faction}</strong></td>\n'
                    html += f'                            <td>{stats["kills"]}</td>\n'
                    html += f'                            <td>{stats["deaths"]}</td>\n'
                    html += f'                            <td class="{kd_class}">{kd:.2f}</td>\n'
                    html += f'                        </tr>\n'

            html += '                    </tbody>\n'
            html += '                </table>\n'
            html += '            </div>\n'

        # Footer
        html += '        </div>\n'
        html += '        <div class="footer">\n'
        html += f'            <p>Report generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>\n'
        html += '            <p>Powered by Enhanced Server Logging Mod</p>\n'
        html += '        </div>\n'
        html += '    </div>\n'

        # JavaScript for search functionality
        html += """
    <script>
        function filterTable(tableId, searchId) {
            const input = document.getElementById(searchId);
            const filter = input.value.toLowerCase();
            const table = document.getElementById(tableId);
            const rows = table.getElementsByTagName('tr');

            for (let i = 1; i < rows.length; i++) {
                const cells = rows[i].getElementsByTagName('td');
                let found = false;

                for (let j = 0; j < cells.length; j++) {
                    const cell = cells[j];
                    if (cell) {
                        const textValue = cell.textContent || cell.innerText;
                        if (textValue.toLowerCase().indexOf(filter) > -1) {
                            found = true;
                            break;
                        }
                    }
                }

                rows[i].style.display = found ? '' : 'none';
            }
        }
    </script>
</body>
</html>
"""

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"HTML report generated: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='Parse Reforger Enhanced Server Logging JSON logs and generate statistics'
    )
    parser.add_argument(
        'log_directory',
        type=str,
        help='Path to directory containing JSON log files (e.g., ServerProfile/flabby/2025/12/10)'
    )
    parser.add_argument(
        '-r', '--recursive',
        action='store_true',
        help='Recursively search for JSON files in subdirectories'
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        default='mission_report',
        help='Output file prefix (default: mission_report)'
    )
    parser.add_argument(
        '--pve',
        action='store_true',
        help='PvE mode: Focus on survival stats (player deaths from AI) instead of kill counts'
    )

    args = parser.parse_args()

    # Parse logs
    log_parser = LogParser(args.log_directory, pve_mode=args.pve)
    log_parser.parse_directory(recursive=args.recursive)

    # Generate reports
    output_dir = Path.cwd()

    # Text report
    text_report = log_parser.generate_text_report()
    print("\n" + text_report)

    text_file = output_dir / f"{args.output}.txt"
    with open(text_file, 'w', encoding='utf-8') as f:
        f.write(text_report)
    print(f"\nText report saved to: {text_file}")

    # CSV exports
    player_csv = output_dir / f"{args.output}_players.csv"
    log_parser.export_player_stats_csv(player_csv)

    weapon_csv = output_dir / f"{args.output}_weapons.csv"
    log_parser.export_weapon_stats_csv(weapon_csv)

    # HTML report
    html_file = output_dir / f"{args.output}.html"
    log_parser.generate_html_report(html_file)

    print("\n✓ Stats generation complete!")
    print(f"\nGenerated files:")
    print(f"  - {text_file.name} (text summary)")
    print(f"  - {html_file.name} (interactive HTML report)")
    print(f"  - {player_csv.name} (player stats CSV)")
    print(f"  - {weapon_csv.name} (weapon stats CSV)")


if __name__ == '__main__':
    main()
