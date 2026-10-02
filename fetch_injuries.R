library(nflreadr)
library(dplyr)
library(readr)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript fetch_injuries.R <year> <outfile>")
}
year <- as.integer(args[1])
outfile <- args[2]

tryCatch({
  df <- load_injuries(seasons = year)
  # A player can have multiple report revisions within the same week (status
  # updated day to day, or listed under two teams in a trade week). Older
  # seasons expose a `date_modified` timestamp distinguishing revisions;
  # keep only the latest one per (team, player, position, week) so
  # downstream (year, week, team, player_name, position) stays a real
  # unique key. Newer seasons (2025+) don't have `date_modified` in this
  # schema -- fall back to just taking one row per key (drops exact dupes;
  # any real same-week revisions are indistinguishable without a timestamp).
  group_cols <- c("season", "week", "team", "gsis_id", "position")
  if ("date_modified" %in% names(df)) {
    df <- df %>%
      group_by(across(all_of(group_cols))) %>%
      slice_max(date_modified, n = 1, with_ties = FALSE) %>%
      ungroup()
  } else {
    df <- df %>%
      group_by(across(all_of(group_cols))) %>%
      slice_head(n = 1) %>%
      ungroup()
  }
  write_csv(df, outfile)
  cat(sprintf("Wrote %d rows to '%s'\n", nrow(df), outfile))
}, error = function(e) {
  cat(sprintf("No injury data available for %d: %s\n", year, conditionMessage(e)))
})
