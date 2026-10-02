library(nflreadr)
library(readr)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: Rscript fetch_playerids.R <outfile>")
}
outfile <- args[1]

df <- load_ff_playerids()
write_csv(df[, c("gsis_id", "fantasypros_id", "sleeper_id")], outfile)
cat(sprintf("Wrote %d rows to '%s'\n", nrow(df), outfile))
