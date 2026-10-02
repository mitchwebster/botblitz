library(nflreadr)
library(dplyr)
library(readr)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript fetch_schedule.R <year> <outfile>")
}
year <- as.integer(args[1])
outfile <- args[2]

# Unlike weekly_stats' opponent_team (only populated for games already
# played), the schedule covers every week of the season up front -- this is
# what lets the UI show "vs OPP" for future weeks, not just past ones.
sched <- load_schedules(seasons = year)

home <- sched %>% transmute(year = season, week, team = home_team, opponent = away_team, is_home = TRUE)
away <- sched %>% transmute(year = season, week, team = away_team, opponent = home_team, is_home = FALSE)
combined <- bind_rows(home, away) %>% filter(!is.na(team), !is.na(opponent))

write_csv(combined, outfile)
cat(sprintf("Wrote %d rows to '%s'\n", nrow(combined), outfile))
