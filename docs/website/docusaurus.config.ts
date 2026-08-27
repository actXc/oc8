import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'oc8 Documentation',
  tagline: 'Build, govern, and run AI-powered work — on infrastructure you control.',
  favicon: 'img/favicon.ico',

  url: 'https://docs.oc8.dev',
  baseUrl: '/',

  organizationName: 'oc8',
  projectName: 'oc8',

  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',

  markdown: {
    mermaid: true,
  },

  themes: ['@docusaurus/theme-mermaid'],

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          path: '..',
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl: undefined,
          exclude: [
            'website/**',
            '**/superpowers/**',
            '**/gap_analysis/**',
            '**/paperclip/**',
            '**/oc8_release_completion/**',
            '**/oc8_auth/**',
            'oc8_Technical_Specification.md',
            'oc8_release_completion_spec.md',
            'oc8_release_completion_agent_prompt.md',
            'oc8_license_and_repo_structure.md',
            'oc8_community_enterprise_feature_matrix.md',
            'oc8_auth_and_multitenancy_split_spec.md',
            'oc8_feature_ledger_usage.md',
            'EXPORT.md',
            'paperclip_analysis.md',
          ],
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      defaultMode: 'dark',
      disableSwitch: false,
      respectPrefersColorScheme: false,
    },
    image: 'img/octopus_oc8.svg',
    navbar: {
      title: '',
      logo: {
        alt: 'oc8',
        src: 'img/oc8_Logo.svg',
        srcDark: 'img/oc8_Logo_white.svg',
        height: 28,
        width: 88,
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'userSidebar',
          label: 'User',
          position: 'left',
        },
        {
          type: 'docSidebar',
          sidebarId: 'installSidebar',
          label: 'Install & Maintain',
          position: 'left',
        },
        {
          type: 'docSidebar',
          sidebarId: 'developerSidebar',
          label: 'Developer',
          position: 'left',
        },
        {
          type: 'docSidebar',
          sidebarId: 'contributorSidebar',
          label: 'Contributing',
          position: 'left',
        },
        {
          href: 'https://github.com/oc8/oc8',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      copyright: `© ${new Date().getFullYear()} oc8`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'toml', 'json', 'python', 'typescript'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
