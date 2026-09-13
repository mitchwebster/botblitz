library(ffpros)
library(dplyr)
library(readr)
library(purrr)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  stop("Usage: Rscript fetch_projections.R <year> <weeks: 'draft' or '1,2,3,...'> <outfile>")
}
year <- as.integer(args[1])
weeks <- strsplit(args[2], ",")[[1]]
outfile <- args[3]

positions <- c("qb", "rb", "wr", "te", "k", "dst")

fetch_one <- function(pos, week) {
  is_draft <- identical(week, "draft")
  tryCatch({
    df <- if (is_draft) {
      fp_projections(pos, year = year, scoring = "PPR")
    } else {
      fp_projections(pos, year = year, week = as.integer(week), scoring = "PPR")
    }
    df$position <- toupper(pos)
    df$week <- week
    df$year <- year
    df
  }, error = function(e) {
    cat(sprintf("Warning: failed to fetch %s projections for year %d week %s: %s\n",
                pos, year, week, conditionMessage(e)))
    NULL
  })
}

all_parts <- list()
for (week in weeks) {
  for (pos in positions) {
    df <- fetch_one(pos, week)
    if (!is.null(df) && nrow(df) > 0) {
      all_parts[[length(all_parts) + 1]] <- df
    }
  }
}

if (length(all_parts) == 0) {
  cat("No projections collected; writing empty file.\n")
  write_csv(data.frame(), outfile)
} else {
  combined <- bind_rows(all_parts)
  write_csv(combined, outfile)
  cat(sprintf("Wrote %d rows to '%s'\n", nrow(combined), outfile))
}
