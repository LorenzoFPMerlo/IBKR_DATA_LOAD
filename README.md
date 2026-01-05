# IBKR DATA LOADER

## Goal
Single-ended application development (named "LOAD") for extracting any existing available data for any given IBKR covered financial instrument through API connection to TWS and IB Gateway (prioritize development on IB Gateway for big dataset extraction). Application must callable and importable into other projects as main data loader.

## Data source
Access to IBKR real-time and historical market and financial data depends on account's "Market data subscription" from Interactive Brokers (IBKR). Pricing and data bundle subscription availability go to: https://www.interactivebrokers.ca/en/accounts/fees/marketData.php?ib_entity=ca 

# API Reference
Reference for all of the TWS API classes go to:
https://ibkrcampus.com/campus/ibkr-api-page/twsapi-ref/#acctsumtags-ref

## Scope
The application must load, fetch, extract any possible data available on a given financial instrument identified by three tokens and inputed as one of the two following tokens: ISIN or ticker. The application must ensure that we identify every existing data entry point linked to the financial instrument and look at the fields of data entry points for the derivatives instrument linked to the financial instrument as well with a different ticker and ISIN but with the underlying asset link (e.g. for stock and ETFs, go for instance into a class named option_data_loader and fetch all available and existing data (e.g. option chains, option contracts open interest/volume). The scope is only the raw data and no computation of financial metrics is to be done in this application/package/program. Why is this? Here we look at what financial data IBKR can recover for us and make the scope within the IBKR API data coverage at the maximum capacity of any user with full subscription. For computing financial metrics and ratios, we will conceive another application/package/program dedicated for it sourcing the data from this very application of IBKR data loader.
