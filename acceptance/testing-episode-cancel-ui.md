# story: testing-episode-cancel-ui [ui]
As a Hive operator I can cancel a running testing episode from the Tests view so that expensive or runaway browser sessions stop without reaching for the API.

## Rules
- While the latest testing episode is refreshing, sweeping, or confirming, the Tests view exposes an obvious cancel control.
- Using the cancel control marks the episode cancelled.
- Pending episode tasks are cancelled and running episode tasks receive a cancellation request.

## Examples
- Given I start a testing episode from the Tests tab
  When the latest episode is still sweeping
  Then I can cancel it from that same Tests tab.
- Given I cancel the episode from the UI
  Then the latest episode changes to cancelled and no new episode tasks are delivered afterward.
