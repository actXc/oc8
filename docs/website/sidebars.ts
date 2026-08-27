import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  userSidebar: [
    'index',
    {
      type: 'category',
      label: 'Start here',
      items: [
        'user/quickstart',
        'user/what-is-oc8',
        'user/key-concepts',
        'user/ui/how-work-moves',
        'user/getting-started',
        'user/daily-workflow',
        'user/governance-and-approvals',
      ],
    },
    {
      type: 'category',
      label: 'UI guide',
      link: {type: 'doc', id: 'user/ui/index'},
      items: [
        'user/ui/office',
        'user/ui/my-work',
        'user/ui/departments',
        'user/ui/agents',
        'user/ui/skills',
        'user/ui/handoffs',
        'user/ui/flows',
        'user/ui/capas',
        'user/ui/knowledge',
        'user/ui/activity',
        'user/ui/costs',
        {
          type: 'category',
          label: 'Settings',
          items: [
            'user/ui/access-and-roles',
            'user/ui/users',
            'user/ui/models',
            'user/ui/credentials',
            'user/ui/audit',
            'user/ui/settings-general',
          ],
        },
        'user/ui/welcome',
        'user/ui/profile',
        'user/ui/copilot',
      ],
    },
    {
      type: 'category',
      label: 'Integrations',
      link: {type: 'doc', id: 'user/integrations/index'},
      items: [
        'MICROSOFT365',
        'GOOGLE_WORKSPACE',
        'HELPDESK_AGENT',
      ],
    },
  ],

  installSidebar: [
    {
      type: 'category',
      label: 'Install and maintain',
      link: {type: 'doc', id: 'user/install-and-maintain/index'},
      items: [
        'DEPLOY',
        'BACKUP_RESTORE',
        'PILOT_BACKUP_RESTORE',
        'SCOPE_AND_LIMITATIONS',
      ],
    },
  ],

  developerSidebar: [
    {
      type: 'category',
      label: 'Developer documentation',
      link: {type: 'doc', id: 'developer/index'},
      items: [
        'developer/tutorial-first-plugin',
        'developer/plugin-types',
        'developer/manifest-reference',
        'developer/architecture-overview',
        'developer/guardrails-and-permissions',
        'developer/setup-forms-and-oauth',
        'developer/dependencies-and-requirements',
        'developer/packaging-and-distribution',
        'developer/claude-plugin-capas',
        'developer/testing-plugins',
      ],
    },
  ],

  contributorSidebar: [
    {
      type: 'category',
      label: 'Contributing',
      link: {type: 'doc', id: 'contributing/index'},
      items: [
        'contributing/architecture',
        'contributing/coding-guidelines',
        'contributing/git-guidelines',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: ['GETTING_STARTED'],
    },
  ],
};

export default sidebars;
