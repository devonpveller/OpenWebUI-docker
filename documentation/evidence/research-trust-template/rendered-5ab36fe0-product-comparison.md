<!-- Rendered 2026-09-12 through THIS branch's `product-comparison` template and THIS branch's fidelity
     check, using the deployed LiteLLM path (qwen36-27b, temperature 0.2) - the same two steps a
     live run performs, in the same order. The synthesis is the recorded one from recorded job 5ab36fe0 (plain git hosting versus Azure DevOps);
     nothing in it was edited. The Sources list and the footer are appended by renderResult and
     are not part of this render.

     Here the table IS the comparison grid - one row per criterion, one column per option, the
     options' real names in the header - which is what the skeleton's 'findings by area' becomes
     when the area is a criterion and the reader is choosing.

     What the pipeline recorded for this document:
       render fidelity : {"checked":25,"units":26,"unchecked":1,"stronger":1,"unsupported":2,"rewritten":0,"replaced":1}
       grounding diff  : numbers [] urls [] names ["VCS"]

     N and M are both counted on THIS file by `countUnits`; report-doc.test.ts asserts it. -->

# Azure DevOps Functions as a Broader ALM Platform with YAML-in-Repo Pipelines Structurally Parallel to GitHub Actions, but the Evidence Covers Only the Architectural Outline

## Executive summary

The evidence positions Azure DevOps as an application-lifecycle-management (ALM) platform analogous to GitLab's "enterprise-grade DevOps platform" scope, rather than a bare git host, and shows that its pipeline definitions are stored as YAML files inside the repository alongside code—structurally parallel to how GitHub Actions uses `.github/workflows/` [Source 2, 3]. Azure Portal provides role-based access control (RBAC) to assign granular permissions and control who manages resources at each level across teams and services. [Source 4, 5] The answer would change materially if the sources covered Azure Boards workflows, branch-protection configuration, or the terminal-versus-UI day-to-day split, none of which they do.

## Comparison at a glance

**Plain Git (GitHub / GitLab).** GitHub is described as "at heart a Git repository hosting service" that additionally implements code review (pull requests, diffs, review requests), project management (issue tracking and assignment), integrations with other developer tools, team management, documentation, and "social coding" [Source 1]. GitLab is characterised as an "enterprise-grade DevOps platform that goes beyond Git repository management," integrating issue tracking, CI/CD, code review, security testing, and Kubernetes management into one application [Source 2]. The underlying git workflow—branch, commit, push, pull request, collaborate, merge—remains the same distributed version-control model in which every developer holds a full copy of the repository [Source 6]. No pricing or licensing terms are stated in the sources.

**Azure DevOps (Azure Repos / Boards / Pipelines).** The evidence shows that Azure DevOps pipeline definitions are stored as YAML files within the repository (e.g., `.vsts-dotnet-ci.yml`, an `azure-pipelines/` folder), version-controlled alongside the code in the same way GitHub Actions workflows are [Source 3]. Azure Portal provides role-based access control (RBAC) to assign granular permissions and control who manages resources at each level across teams and services [Source 4, 5]. The sources infer that the Repos/Boards/Pipelines triad represents a broader ALM platform rather than a bare git host, analogous to GitLab's integrated positioning [Source 2]. No pricing, licensing, or per-seat terms are stated in the sources.

## Options by criterion

| Criterion | Plain Git (GitHub / GitLab) | Azure DevOps | Source |
|---|---|---|---|
| Core version-control model | Distributed VCS; every developer holds a full copy of every commit, branch, and file [Source 6] | Git repositories hosted within the platform; the same distributed git model applies to the repo itself [Source 6, 3] | [Source 6, 3] |
| Basic developer workflow | Branch → commit → push → pull request → collaborate → merge into main [Source 6] | Same git-level workflow at the repository layer; pipeline definitions live as YAML in the repo [Source 6, 3] | [Source 6, 3] |
| CI/CD definition location | GitHub Actions: `.github/workflows/` directory in the repo [Source 3]; GitLab integrates CI/CD into the platform [Source 2] | YAML pipeline files (`.vsts-dotnet-ci.yml`, `azure-pipelines/` folder) committed in the repo [Source 3] | [Source 3, 2] |
| Platform scope beyond the repo | GitHub: code review, issue tracking, integrations, team management, documentation, "social coding" [Source 1]; GitLab: issue tracking, CI/CD, code review, security testing, Kubernetes management [Source 2] | Inferred as a broader ALM platform (Repos/Boards/Pipelines triad) analogous to GitLab's integrated scope [Source 2] | [Source 1, 2] |
| Access control / permissions | GitHub provides team management features [Source 1] | Azure Portal RBAC assigns granular permissions across teams and services at each level [Source 4, 5] | [Source 1, 4, 5] |
| Concurrent CI/CD definitions in one repo | A project can maintain `.github/workflows/` alongside other pipeline definitions [Source 3] | The same MSBuild repository contains both `.github/workflows/` and `.azuredevops/azure-pipelines/` directories, indicating the two systems can coexist in one repo [Source 3] | [Source 3] |

## What the evidence does not settle

The sources confirm that Azure DevOps pipelines are YAML-in-repo and that the platform is broader than a bare git host, but they do not resolve several points a buyer would need to compare the two options concretely. The inference that the Repos/Boards/Pipelines triad is "analogous" to GitLab's integrated scope [Source 2] is drawn from GitLab's description and the structural parallel of YAML pipelines [Source 3]; it is not a direct statement from an Azure DevOps source about what Boards or Pipelines specifically contain. The MSBuild example [Source 3] shows that both CI/CD systems can coexist in one repository, but it does not establish whether a team would run them concurrently in production or use one as a migration target. The RBAC description [Source 4, 5] speaks to Azure Portal resource management generally; it does not specify how permissions map to individual Azure Repos repositories, Boards work-item lists, or pipeline scopes.

## Limitations and open questions

The evidence is thin on the specific architectural components, workflow mechanics, and day-to-day usage patterns the question asks about. The following points are not addressed by any source:

- What are Azure Boards work items, sprints, backlogs, and work-item-driven development workflows, and how do they change a team's planning cadence compared to issue trackers on GitHub or GitLab?
- What are Azure Repos-specific features such as branch protection rules, pull-request approval policies, and the distinction between Azure Repos Git and TFVC (Team Foundation Version Control)?
- How do Azure Pipelines build agents (hosted vs. self-hosted), environments/stages, and the distinction between YAML pipeline-as-code and classic UI-based release pipelines work in practice?
- What are Azure DevOps service connections, artifact feeds (e.g., Azure Artifacts), and service hooks, and how do they integrate with the rest of the platform?
- How does identity and single sign-on integration with Azure AD or Microsoft Entra ID affect authentication and authorization in Azure DevOps?
- How does a team's day-to-day terminal workflow (git clone, git push, git tag, etc.) change when migrating from a pure git + pull-request workflow to Azure DevOps?
- What is the mental-model shift from treating git as a distributed version-control tool to treating Azure DevOps as an end-to-end ALM/DevOps platform, including work-item-driven development, release management, and environment promotion?
- What are the specific limitations or gotchas of Azure DevOps, such as branch-policy constraints, PR approval policy configuration, pipeline YAML versus classic release pipeline trade-offs, or identity federation issues?
- What do developers do differently in the terminal versus the Azure DevOps web UI on a day-to-day basis?

A further run focused on Azure Boards work-item workflows, Azure Repos branch and pull-request policies, Azure Pipelines agent and environment configuration, and the terminal-versus-UI day-to-day split would close this.
