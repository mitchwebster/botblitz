library(nflreadr)
library(dplyr)
library(readr)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("Usage: Rscript fetch_stats.R <year> <outfile_offense_week> <outfile_offense_season> <outfile_dst_week> <outfile_dst_season>")
}
year <- as.integer(args[1])
outfile_offense_week <- args[2]
outfile_offense_season <- args[3]
outfile_dst_week <- args[4]
outfile_dst_season <- args[5]

fetch_offense <- function(summary_level, outfile) {
  tryCatch({
    offense <- load_player_stats(seasons = year, summary_level = summary_level)
    offense <- offense[offense$position %in% c("QB", "RB", "WR", "TE", "K"), ]
    write_csv(offense, outfile)
    cat(sprintf("Wrote %d offense (%s) rows to '%s'\n", nrow(offense), summary_level, outfile))
  }, error = function(e) {
    cat(sprintf("No offense (%s) stats available for %d: %s\n", summary_level, year, conditionMessage(e)))
    write_csv(data.frame(), outfile)
  })
}

fetch_offense("week", outfile_offense_week)
fetch_offense("reg", outfile_offense_season)

# --- DST: team defense actuals + points allowed (from schedules), weekly and season ---
fetch_dst <- function(outfile_week, outfile_season) {
  tryCatch({
    team_stats <- load_team_stats(seasons = year)
    sched <- load_schedules(seasons = year)

    # Points allowed = opponent's score in that game.
    home_pa <- sched %>% transmute(season, week, team = home_team, points_allowed = away_score)
    away_pa <- sched %>% transmute(season, week, team = away_team, points_allowed = home_score)
    pa <- bind_rows(home_pa, away_pa)

    dst_week <- team_stats %>%
      select(season, week, team, opponent_team,
             def_sacks, def_interceptions, def_fumbles, def_tds, def_safeties) %>%
      left_join(pa, by = c("season", "week", "team"))

    write_csv(dst_week, outfile_week)
    cat(sprintf("Wrote %d DST (week) rows to '%s'\n", nrow(dst_week), outfile_week))

    dst_season <- dst_week %>%
      group_by(season, team) %>%
      summarize(
        games = n(),
        def_sacks = sum(def_sacks, na.rm = TRUE),
        def_interceptions = sum(def_interceptions, na.rm = TRUE),
        def_fumbles = sum(def_fumbles, na.rm = TRUE),
        def_tds = sum(def_tds, na.rm = TRUE),
        def_safeties = sum(def_safeties, na.rm = TRUE),
        points_allowed = sum(points_allowed, na.rm = TRUE),
        .groups = "drop"
      )
    write_csv(dst_season, outfile_season)
    cat(sprintf("Wrote %d DST (season) rows to '%s'\n", nrow(dst_season), outfile_season))
  }, error = function(e) {
    cat(sprintf("No DST stats available for %d: %s\n", year, conditionMessage(e)))
    write_csv(data.frame(), outfile_week)
    write_csv(data.frame(), outfile_season)
  })
}

fetch_dst(outfile_dst_week, outfile_dst_season)
