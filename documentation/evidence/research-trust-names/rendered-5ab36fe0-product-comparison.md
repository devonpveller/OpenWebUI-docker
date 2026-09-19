<!-- The `product-comparison` render of recorded job 5ab36fe0 (plain git hosting versus Azure DevOps), as delivered by THIS
     branch's pipeline: the document research-trust-template committed at e28c974, put through
     the fidelity check with the names gate and the FLIP JUDGE. Not a fresh render - the diff
     against e28c974 is the check's work and nothing else, and every model call in it went
     through the deployed LiteLLM path (3 calls, 10s).

     EVERY CHANGED LINE, ATTRIBUTED - which half of the check made it, and the polarity before
     and after. No hunk flips a sentence's polarity; an absence stays an absence:

     | line | changed by | polarity | the text that changed |
     |------|------------|----------|-----------------------|
     | (none) | - | - | the check had nothing to correct in this document |

     What the pipeline recorded for this document:
       render fidelity : {"checked":25,"units":25,"unchecked":0,"stronger":0,"unsupported":0,"rewritten":0,"replaced":0,"polarity_skipped":0,"polarity_default":0,"duplicate_skipped":0,"no_candidate":0,"names_blocked":[]}
       polarity decided by : {"no-negation":18,"evidence-noun":2,"default-absence":1,"heading":4}
       grounding diff  : names [] -> []

     `polarity decided by` follows the DECIDER, not the words: a unit put to the flip judge is
     recorded as "judge" and leaves the lexical bucket it was provisionally filed under, so
     what remains under a lexical key is exactly what the check never had to ask about. Three
     lexicons in a row decided polarity wrongly on delivered documents; nothing lexical decides
     it now.

     Applying the check to THIS file returns it byte for byte, and a second pass changes nothing. -->

# Plain Git Hosts and Azure DevOps Share a YAML-in-Repo CI/CD Substrate, with Azure DevOps Positioned as a Broader ALM Platform

## Executive summary

The evidence shows that the CI/CD layer of a plain git host (GitHub, GitLab) and Azure DevOps is structurally parallel: both store pipeline definitions as YAML committed inside the repository, and a single project can maintain both sets of definitions concurrently [Source 3]. Beyond that shared substrate, the sources position Azure DevOps as a broader application-lifecycle-management (ALM) platform analogous to GitLab's "enterprise-grade DevOps platform" framing, whereas GitHub is described as "at heart a Git repository hosting service" with added collaboration features [Source 1, 2]. For a buyer evaluating whether to adopt Azure DevOps over a plain git host, the evidence confirms the architectural direction (wider platform scope, role-based access control via Azure Portal [Source 4, 5]) but does not detail the specific Azure DevOps features—Boards work items, Pipelines build agents, environments, service connections, or identity integration—that would drive a day-to-day workflow decision. The answer would change materially once those feature-level details are available.

## Comparison at a glance

**Plain git host (GitHub / GitLab).** GitHub is "at heart a Git repository hosting service" that additionally implements code review (pull requests, diffs, review requests), project management (issue tracking and assignment), integrations with other developer tools, team management, documentation, and "social coding" [Source 1]. It centralizes code, version history, and discussion so teams coordinate contributions without duplicating work [Source 7]. GitLab is described as an "enterprise-grade DevOps platform that goes beyond Git repository management," integrating issue tracking, CI/CD, code review, security testing, and Kubernetes management into one application [Source 2]. The underlying git workflow—creating a branch, committing, pushing, opening a pull request, collaborating, and merging into main—remains the same distributed model in which every developer holds a full copy of the repository [Source 6].

**Azure DevOps.** The evidence shows that Azure DevOps pipelines are defined as YAML files committed within the repository (e.g., `.vsts-dotnet-ci.yml`, an `azure-pipelines/` folder), version-controlled alongside the code in the same way GitHub Actions uses `.github/workflows/` [Source 3]. The MSBuild repository on GitHub contains both a `.github/workflows` directory and an `.azuredevops/azure-pipelines` directory, demonstrating that the two CI/CD systems are structurally parallel and can coexist in one repository [Source 3]. The pattern of integrating CI/CD, issue tracking, code review, and security testing into a single platform (as GitLab does) is analogous to what Azure DevOps does with its Repos/Boards/Pipelines triad, representing a broader ALM platform rather than a bare git host [Source 2]. Azure Portal provides role-based access control (RBAC) to assign granular permissions and control who manages resources at each level across teams and services [Source 4, 5].

## Options by criterion

| Criterion | Plain git host (GitHub / GitLab) | Azure DevOps | Source |
|---|---|---|---|
| CI/CD pipeline definition format | GitHub Actions uses in-repo YAML (`.github/workflows/`) [Source 1, 3] | YAML committed in-repo (`.vsts-dotnet-ci.yml`, `azure-pipelines/` folder), version-controlled alongside code [Source 3] | [Source 1, 2, 3] |
| Concurrent CI/CD operation | A project can maintain GitHub Actions workflows alongside Azure DevOps pipeline definitions in the same repository [Source 3] | Same: the MSBuild repository on GitHub contains both `.github/workflows` and `.azuredevops/azure-pipelines` directories, showing the two systems can run concurrently [Source 3] | [Source 3] |
| Platform scope (as described in sources) | GitHub: "at heart a Git repository hosting service" plus code review, project management, integrations, team management, documentation, "social coding" [Source 1]. GitLab: "enterprise-grade DevOps platform that goes beyond Git repository management" integrating issue tracking, CI/CD, code review, security testing, Kubernetes management [Source 2] | Analogous to GitLab's broader ALM approach: Repos/Boards/Pipelines triad representing a broader ALM platform rather than a bare git host [Source 2, inferred] | [Source 1, 2, 3] |
| Access control model | GitHub provides team management features [Source 1] | Azure Portal provides role-based access control (RBAC) to assign granular permissions and control who manages resources at each level across teams and services [Source 4, 5] | [Source 1, 4, 5] |
| Underlying version-control model | Distributed: every developer has a full copy of the entire repository (every commit, branch, and file), enabling easy branching and merging [Source 6] | Not separately described in the sources; the pipeline layer is YAML-in-repo [Source 3] | [Source 3, 6] |

## What the evidence does not settle

The sources confirm that Azure DevOps and plain git hosts share a YAML-in-repo CI/CD substrate and that Azure DevOps is positioned as a broader ALM platform [Source 2, 3]. However, the evidence does not describe the specific Azure DevOps components that would differentiate day-to-day use: Azure Boards work items and sprints, Azure Pipelines build agents (hosted versus self-hosted), environments and stages, the distinction between YAML pipeline-as-code and classic UI-based release pipelines, service connections, artifact feeds, or service hooks. The sources also do not address how a team's terminal workflow (clone, push, tag, branch) changes on migration, nor the mental-model shift from treating git as a distributed version-control tool to treating Azure DevOps as an end-to-end ALM platform. The one concrete architectural detail the sources do provide—RBAC via Azure Portal [Source 4, 5]—is stated at the portal level and does not specify how it maps to repository-level or pipeline-level permissions within Azure DevOps itself.

## Limitations and open questions

The evidence is thin on the specific Azure DevOps features, workflow changes, and operational gotchas that the question targets. The following points are not covered by any source:

- What are Azure Boards work items, sprints, backlogs, and the work-item-driven development workflow in practice?
- What are Azure Repos-specific features such as branch protection rules, pull-request approval policies, and the distinction between Azure Repos Git and TFVC (Team Foundation Version Control)?
- How do Azure Pipelines build agents (hosted vs. self-hosted), environments/stages, and the choice between YAML pipeline-as-code and classic UI-based release pipelines work?
- What are Azure DevOps service connections, artifact feeds (e.g., Azure Artifacts), and service hooks, and how do they function?
- How does identity and single sign-on integration with Azure AD or Microsoft Entra ID work in the context of Azure DevOps authentication and authorization?
- How does a team's day-to-day terminal workflow (git clone, git push, git tag, etc.) change when migrating from a pure git + pull-request workflow to Azure DevOps?
- What is the mental-model shift from treating git as a distributed version-control tool to treating Azure DevOps as an end-to-end ALM/DevOps platform (work-item-driven development, release management, environment promotion)?
- What are the specific limitations or gotchas of Azure DevOps (branch policy constraints, PR approval policy configuration, pipeline YAML vs. classic release pipeline trade-offs, identity federation issues)?
- What do developers do differently in the terminal versus the Azure DevOps web UI on a day-to-day basis?

A further run focused on the specific Azure DevOps feature set (Boards work items, Pipelines agents and environments, Repos branch policies, service connections, and Entra ID integration) and the resulting day-to-day terminal-versus-UI workflow would close this.
