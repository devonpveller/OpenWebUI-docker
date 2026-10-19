# Vendored: OB1 `integrations/connectors/connector_kit`

A byte copy of the connector kit (security plan step 2) at OB1 **ad0b2de08063f9bb51990dfca7ec1705614c87d7**
(`work/search-gate`, the kit tip 8c163e0 plus the opt-in `return_clean_text`), the commit the ai-stack
OB1 gitlink points at.

Why vendored: the search gate is an ai-stack image built from `search/gateway/` alone; it must not
depend on an OB1 checkout at run time. Do not edit these files here: change the kit in OB1, bump the
gitlink, then re-copy (`git -C OB1 archive <sha> integrations/connectors/connector_kit | tar -x -C
search/gateway/src/connector_kit --strip-components=3`) and update the sha above.
`tests/test_kit_vendored.py` fails when a file here differs from the OB1 checkout or the sha above is
not the gitlink.
