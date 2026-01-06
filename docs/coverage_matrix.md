# Coverage Matrix (Phase 0)

| Dataset family | Dataset name | IB request type | Instrument types supported | Status | Capability probe | Entitlement/subscription notes |
| --- | --- | --- | --- | --- | --- | --- |
| Security definition | matching_symbols | reqMatchingSymbols | stock, etf, index, future, option, bond | stub | security_def | Requires IBKR symbol matching support; available without market data subscription. |
| Security definition | contract_details | reqContractDetails | stock, etf, index, future, option, bond, forex, cfd, mutual_fund, warrant, structured_product | stub | security_def | Contract details are generally available; some fields depend on exchange. |
| Security definition | market_rule | reqMarketRule | stock, etf, index, future, option | stub | security_def | Requires marketRuleId from contract details. |
| Options | option_chain_discovery | reqSecDefOptParams | stock, etf, index | stub | options_data | Requires options data permission for full coverage. |
| Futures | futures_chain_discovery | reqContractDetails | future | stub | security_def | Requires exchange-specific search logic. |
| Market data (snapshot) | live_quote_snapshot | reqMktData(snapshot) | stock, etf, index, future, option, forex, cfd | stub | market_data | Requires market data subscriptions; snapshot pacing limits apply. |
| Market data (streaming) | live_top_of_book | reqMktData(streaming) | stock, etf, index, future, option, forex | stub | market_data | Requires streaming entitlements; pacing applies. |
| Market data (streaming) | tick_by_tick_live | reqTickByTickData | stock, etf, index, future, option, forex | stub | market_data | Tick-by-tick permissions vary by exchange. |
| Market depth | market_depth | reqMktDepth/reqMktDepthExchanges | stock, etf, index, future, option | stub | depth_data | Requires depth subscriptions and exchange support. |
| Historical | historical_bars | reqHistoricalData | stock, etf, index, future, option | implemented | historical_data | Historical data availability varies by subscription and instrument. |
| Historical | historical_ticks_trades | reqHistoricalTicks(TRADES) | stock, etf, index, future, option, forex | stub | historical_data | Requires tick history entitlements; limited windows. |
| Historical | historical_ticks_bid_ask | reqHistoricalTicks(BID_ASK) | stock, etf, index, future, option, forex | stub | historical_data | Requires tick history entitlements; limited windows. |
| Historical | historical_ticks_midpoint | reqHistoricalTicks(MIDPOINT) | stock, etf, index, future, option, forex | stub | historical_data | Requires tick history entitlements; limited windows. |
| Options | option_chain_snapshot | reqSecDefOptParams + reqMktData(snapshot) | stock, etf, index | stub | options_data | Requires options market data; batch limits apply. |
| Options | option_contract_bars | reqHistoricalData | option | stub | options_data | Requires option market data subscriptions. |
| Options | option_contract_tick_by_tick | reqTickByTickData | option | stub | options_data | Requires tick-by-tick options entitlement. |
| Fundamentals | fundamentals_report | reqFundamentalData | stock, etf, index | stub | fundamentals | Requires fundamentals subscription. |
| News | news_providers | reqNewsProviders | stock, etf, index, future, option | stub | news | News subscriptions required for premium feeds. |
| News | historical_news | reqHistoricalNews | stock, etf, index, future, option | stub | news | Historical news requires news subscriptions. |
| Corporate actions | corporate_actions | (no direct IB API) | stock, etf, index | unimplemented | market_data | Not provided by a reliable IB API endpoint. |
| Account | account_positions | reqPositions/reqAccountUpdates | stock, etf, index, future, option | unimplemented | account_data | Out of scope for instrument-centric loader. |

## Design notes
IBKR coverage varies by instrument type, exchange, and subscription package. This registry is a finite, curated map of known request families and the data they can produce. Many entries are stubs pending connector integration and execution logic.

## Next steps
Phase 1 will focus on instrument resolution and connector integration. Phase 2 will implement the historical bars engine with chunking, checkpointing, and DQC.