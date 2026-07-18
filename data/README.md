# Data

Drop the Instacart Market Basket Analysis CSVs here (from Kaggle). Each
file becomes a table named after the filename, e.g. `orders.csv` becomes
table `orders`.

Expected files:
- orders.csv
- products.csv
- order_products__prior.csv
- order_products__train.csv
- aisles.csv
- departments.csv

The full dataset is large (millions of rows across order_products files).
For a fast local demo, consider trimming order_products__prior.csv to a
sample before dropping it here, e.g.:

    head -n 500000 order_products__prior.csv > order_products__prior_sample.csv

Then remove the full file so only the sample gets loaded.
