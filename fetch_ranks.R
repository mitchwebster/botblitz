library(dplyr)
library(ffpros)
library(readr)

# Fetch and prepare the general PPR data
ppr_data <- fp_rankings(page = "ppr-cheatsheets") %>%
  select(fantasypros_id, player_name, pos, team, player_bye_week, rank, tier)

# Function to fetch and process position-specific data
process_position_data <- function(page, suffix) {
  position_data <- fp_rankings(page = page) %>%
    select(fantasypros_id,
      position_rank = rank,
      position_tier = tier
    )

  # Rename to avoid conflicts and clarify source
  names(position_data)[-1] <- paste0(names(position_data)[-1], "_", suffix)

  position_data
}

# Fetch data for RB, WR, QB, TE, and DST
rb_data <- process_position_data("ppr-rb-cheatsheets", "RB")
wr_data <- process_position_data("ppr-wr-cheatsheets", "WR")
qb_data <- process_position_data("qb-cheatsheets", "QB")
te_data <- process_position_data("ppr-te-cheatsheets", "TE")
dst_data <- process_position_data("dst-cheatsheets", "DST")
k_data <- process_position_data("k-cheatsheets", "K")

# Join all data
all_data <- ppr_data %>%
  left_join(rb_data, by = "fantasypros_id") %>%
  left_join(wr_data, by = "fantasypros_id") %>%
  left_join(qb_data, by = "fantasypros_id") %>%
  left_join(te_data, by = "fantasypros_id") %>%
  left_join(dst_data, by = "fantasypros_id") %>%
  left_join(k_data, by = "fantasypros_id")

# Coalesce rankings and tiers into single columns
all_data <- all_data %>%
  mutate(
    position_rank = coalesce(position_rank_RB, position_rank_WR, position_rank_QB, position_rank_TE, position_rank_DST, position_rank_K),
    position_tier = coalesce(position_tier_RB, position_tier_WR, position_tier_QB, position_tier_TE, position_tier_DST, position_tier_K)
  ) %>%
  select(-matches("position_rank_|position_tier_")) # remove intermediate columns

# Fetch additional player ID data
additional_data <- read_csv("https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv") %>%
  select(fantasypros_id, gsis_id) %>%
  mutate(fantasypros_id = as.character(as.integer(fantasypros_id))) # Convert fantasypros_id from float to integer

# Join additional_data to all_data on fantasypros_id
all_data <- left_join(all_data, additional_data, by = "fantasypros_id")

# Save the final joined data to CSV
write.csv(all_data, "player_ranks.csv", row.names = FALSE)

# Print confirmation
print("Data has been successfully saved to 'player_ranks.csv'.")
