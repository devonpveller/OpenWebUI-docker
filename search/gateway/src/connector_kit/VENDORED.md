# Vendored: OB1 `integrations/connectors/connector_kit`

A byte copy of the connector kit (security plan step 2) at OB1 **ac7e1221c72f920498a89449c55ba3dc4d2795da**
(`work/search-gate2`: the connector-template round-2 kit tip 1fa4b3a plus the gate's opt-in
`return_clean_text`), the commit the ai-stack OB1 gitlink points at.

Why vendored: the search gate is an ai-stack image built from `search/gateway/` alone; it must not
depend on an OB1 checkout at build or run time. Do not edit these files here: change the kit in OB1,
bump the gitlink, re-copy (`git -C OB1 archive <sha> integrations/connectors/connector_kit | tar -x -C
search/gateway/src/connector_kit --strip-components=3`), then update the sha and the manifest below.

`tests/test_kit_vendored.py` checks (1) every file against this manifest (always - no OB1 needed),
(2) the manifest's files against the GITLINK's tree (`git -C OB1 show <gitlink>:...`, not the working
checkout), and (3) that the sha above is the gitlink.

Manifest (sha256 of each file, LF line endings):

    f85970557c3cc110d21e2740d627d20220a44fb28726b051db4a1eedb95d7896  __init__.py
    0d6c9e519b9a3c95de906158ebe06c16ccdb35f3a9f10523aea7f0195178c1ed  client.py
    90bc54159459a1068d39a5993072052a075e0377d79ec28e7cf66151ee6a339a  connector.py
    e8da6447c3ce3e507b9d94448870e9cc8fbc444d84b1159ca3a701bf3d2bd0cb  envelope.py
    150d3b359ccf8eda2d784221d20e615d04719f0e999f0cb278722a5f94668d8b  outcomes.py
    3456bb63f2a1c07de5ef3152626e0998fccafd56f145af2dd21591f36698ad5e  provenance.py
    92cf664b1184015e7dc3083e8dc38d06c58620e18597c99a5e47e28db2249fed  secret.py
    a8872e7522cae78024f7f4ab44915e6f8b2941752864d41977bf94385dde0ecb  testkit/__init__.py
    161647fc4e3ccefebc93c802a985583223ee26e636e0e7745645938c31cadcf9  testkit/conformance.py
    72cfdef02b96a75a5316a6d6ac25212101872b086982483023f9c0c7485c8429  testkit/contract.py
    f126fe74edd600e90b55e5423f979c62068d4be7c5a8e5fbe74eef6fef199114  testkit/fake_intake.py
    87a799380a6c418686ebc497bcc44e33fcb5c62421f573d69c6f690801abf014  testkit/sandbox.py
    84d9cf85eff0076d6a495f5f1bbf3a5cb120db9848ab009bee7233a0f5da668e  testkit/static_scan.py
