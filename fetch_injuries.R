library(nflreadr)
library(readr)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript fetch_injuries.R <year> <outfile>")
}
year <- as.integer(args[1])
outfile <- args[2]

tryCatch({
  df <- load_injuries(seasons = year)
  write_csv(df, outfile)
  cat(sprintf("Wrote %d rows to '%s'\n", nrow(df), outfile))
}, error = function(e) {
  cat(sprintf("No injury data available for %d: %s\n", year, conditionMessage(e)))
})
