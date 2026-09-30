library(ffanalytics)
library(dplyr)
library(readr)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  stop("Usage: Rscript fetch_ffanalytics_projections.R <year> <week: 0 for preseason> <outfile>")
}
year <- as.integer(args[1])
week <- as.integer(args[2])
outfile <- args[3]

# The three ffanalytics sources confirmed (via manual exploration) to serve real
# season-specific historical data rather than always redirecting to the live/
# current page: FFToday (draft+weekly, ~2010+), FantasySharks (draft+weekly,
# 2018+), ESPN (draft 2018+, weekly 2019+, though 2023 preseason is mostly NA
# on ESPN's own end -- not fixable locally). Every other source in ffanalytics
# (CBS, FanDuel/NumberFire, RTSports, Walterfootball, FantasyPros) was verified
# to ignore the season/week params and always return current live data.
sources <- c("FFToday", "FantasySharks", "ESPN")
positions <- c("QB", "RB", "WR", "TE", "K", "DST")

# Our league is PPR (points_per_reception = 1.0); everything else uses
# ffanalytics' own standard-scoring defaults. Used only for the convenience
# `points` column below -- the raw per-stat columns (pass_yds, rec_tds, ...)
# are kept as-is so a different scoring system can be applied later without
# re-scraping.
scoring_rules <- scoring
scoring_rules$rec$rec <- 1

fetch_one_source <- function(src) {
  # ESPN's DST scrape is missing a column (dst_pts_allowed) that
  # projections_table() needs for VOR/scoring, breaking the whole call.
  # DST coverage still comes from FFToday + FantasySharks, so just drop it
  # for ESPN rather than losing every position over one broken position.
  pos_list <- if (src == "ESPN") setdiff(positions, "DST") else positions
  tryCatch({
    raw <- scrape_data(src = src, pos = pos_list, season = year, week = week)

    # Some sources' own `pos` column doesn't match what we asked for (e.g.
    # FantasySharks reports "D" for DST, not "DST"). Overwriting it before
    # calling projections_table() breaks that function internally (it relies
    # on the untouched pos/list-name agreement -- confirmed by testing: same
    # call, only difference being this relabel, throws "argument is of length
    # zero"). So leave `raw` untouched for projections_table, and instead
    # build our own id -> requested-position lookup to standardize pos
    # afterward, on the output only.
    id_to_pos <- bind_rows(lapply(names(raw), function(requested_pos) {
      d <- raw[[requested_pos]]
      if (is.null(d) || nrow(d) == 0) return(NULL)
      data.frame(id = d$id, pos = requested_pos, stringsAsFactors = FALSE)
    })) %>% distinct(id, .keep_all = TRUE)

    # Raw stat lines, one row per player, all positions stacked (bind_rows
    # fills NA for stat columns that don't apply to a given position, same
    # as how preseason_projections/weekly_projections already work). Drop the
    # source's own (possibly quirky) pos column in favor of id_to_pos below.
    raw_combined <- bind_rows(raw) %>% select(-pos)

    # points/floor/ceiling/rank computed under our league's scoring, joined
    # back onto the raw stat columns by id.
    scored <- tryCatch({
      projections_table(raw, scoring_rules = scoring_rules, avg_type = "average") %>%
        select(id, points, floor, ceiling, points_vor, rank, pos_rank, tier)
    }, error = function(e) {
      cat(sprintf("Warning: could not score %s year %d week %d: %s\n", src, year, week, conditionMessage(e)))
      NULL
    })

    combined <- raw_combined %>% left_join(id_to_pos, by = "id")
    if (!is.null(scored)) {
      combined <- combined %>% left_join(scored, by = "id")
    }

    combined <- add_player_info(combined)
    if (all(c("team.x", "team.y") %in% names(combined))) {
      # add_player_info() joins in player_table's own "team" column on top of
      # the raw scrape's "team" -- prefer the source's own (fresher after a
      # midseason trade) and drop the duplicate.
      combined <- combined %>%
        mutate(team = coalesce(team.x, team.y)) %>%
        select(-team.x, -team.y)
    }
    combined$source <- src
    combined$year <- year
    combined$week <- week
    combined
  }, error = function(e) {
    cat(sprintf("Warning: %s failed for year %d week %d: %s\n", src, year, week, conditionMessage(e)))
    NULL
  })
}

all_parts <- lapply(sources, fetch_one_source)
all_parts <- all_parts[!vapply(all_parts, is.null, logical(1))]

if (length(all_parts) == 0) {
  cat("No projections collected from any source; writing empty file.\n")
  write_csv(data.frame(), outfile)
} else {
  combined <- bind_rows(all_parts)
  write_csv(combined, outfile)
  cat(sprintf("Wrote %d rows (%d sources) to '%s'\n", nrow(combined), length(all_parts), outfile))
}
