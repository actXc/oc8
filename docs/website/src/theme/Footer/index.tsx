import React, {type ReactNode} from 'react';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
import styles from './styles.module.css';

type FooterLink = {
  label: string;
  to?: string;
  href?: string;
  icon?: ReactNode;
};

type FooterColumn = {
  title: string;
  links: FooterLink[];
};

const iconProps = {
  width: 14,
  height: 14,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

function Icon({children}: {children: ReactNode}) {
  return <svg {...iconProps} className={styles.linkIcon}>{children}</svg>;
}

const columns: FooterColumn[] = [
  {
    title: 'Documentation',
    links: [
      {label: 'User docs', to: '/user/'},
      {label: 'Install & maintain', to: '/user/install-and-maintain/'},
      {label: 'Developer', to: '/developer/'},
      {label: 'Contributing', to: '/contributing/'},
    ],
  },
  {
    title: 'Product',
    links: [
      {
        label: 'Ecosystem',
        href: 'https://oc8.ai/ecosystem',
        icon: (
          <Icon>
            <rect x="16" y="16" width="6" height="6" rx="1" />
            <rect x="2" y="16" width="6" height="6" rx="1" />
            <rect x="9" y="2" width="6" height="6" rx="1" />
            <path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3" />
            <path d="M12 12V8" />
          </Icon>
        ),
      },
      {
        label: 'oc8.ai',
        href: 'https://oc8.ai/',
        icon: (
          <Icon>
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </Icon>
        ),
      },
    ],
  },
  {
    title: 'Resources',
    links: [
      {
        label: 'Blog',
        href: 'https://oc8.ai/blog',
        icon: (
          <Icon>
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
          </Icon>
        ),
      },
      {
        label: 'Discord',
        href: 'https://discord.gg/vEYpvzXUv',
        icon: (
          <Icon>
            <path d="M8.5 14.5c.5.5 1.5 1 2.5 1s2-.5 2.5-1" />
            <path d="M15 11.5c.5-.5 1-1.5 1-2.5s-.5-2-1-2.5" />
            <path d="M9 11.5c-.5-.5-1-1.5-1-2.5s.5-2 1-2.5" />
            <path d="M7.5 8.5C8.5 7 10.5 6 12 6s3.5 1 4.5 2.5" />
            <path d="M12 6V4.5a1.5 1.5 0 0 0-3 0V6" />
            <path d="M18 8.5c1 1.5 1.5 3.5 1.5 5.5s-.5 4-1.5 5.5" />
            <path d="M6 8.5C5 10 4.5 12 4.5 14s.5 4 1.5 5.5" />
          </Icon>
        ),
      },
      {
        label: 'Reddit',
        href: 'https://www.reddit.com/r/oc8/',
        icon: (
          <Icon>
            <circle cx="9" cy="13" r="1" />
            <circle cx="15" cy="13" r="1" />
            <path d="M12 20a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z" />
            <path d="M8.5 9.5c.5-1 1.5-1.5 2.5-1.5" />
            <path d="M15.5 9.5c-.5-1-1.5-1.5-2.5-1.5" />
          </Icon>
        ),
      },
      {
        label: 'GitHub',
        href: 'https://github.com/oc8-ai',
        icon: (
          <Icon>
            <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
            <path d="M9 18c-4.51 2-5-2-7-2" />
          </Icon>
        ),
      },
      {
        label: 'X.com',
        href: 'https://x.com/oc8ai',
        icon: (
          <Icon>
            <path d="M4 4l16 16" />
            <path d="M20 4 4 20" />
          </Icon>
        ),
      },
      {
        label: 'LinkedIn',
        href: 'https://www.linkedin.com/company/oc8/',
        icon: (
          <Icon>
            <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-4 0v7h-4v-7a6 6 0 0 1 6-6Z" />
            <rect x="2" y="9" width="4" height="12" />
            <circle cx="4" cy="4" r="2" />
          </Icon>
        ),
      },
    ],
  },
  {
    title: 'Company',
    links: [
      {label: 'About', href: 'https://oc8.ai/about'},
      {label: 'Contact', href: 'https://oc8.ai/contact'},
      {label: 'Legal notice', href: 'https://oc8.ai/legal'},
      {label: 'Privacy', href: 'https://oc8.ai/privacy'},
    ],
  },
];

function FooterLinkItem({link}: {link: FooterLink}) {
  const className = styles.footerLink;
  const content = (
    <>
      {link.icon}
      <span>{link.label}</span>
    </>
  );

  if (link.to) {
    return (
      <Link className={className} to={link.to}>
        {content}
      </Link>
    );
  }

  return (
    <a
      className={className}
      href={link.href}
      target="_blank"
      rel="noopener noreferrer">
      {content}
    </a>
  );
}

export default function Footer(): JSX.Element {
  const logoDark = useBaseUrl('/img/oc8_Logo_white.svg');
  const logoLight = useBaseUrl('/img/oc8_Logo.svg');
  const year = new Date().getFullYear();

  return (
    <footer className={styles.footer}>
      <div className={styles.gradientLine} aria-hidden="true" />
      <div className={styles.inner}>
        <div className={styles.grid}>
          <div className={styles.brand}>
            <Link to="/" className={styles.logoLink} aria-label="oc8 Documentation home">
              <img src={logoDark} alt="" className={`${styles.logo} ${styles.logoDark}`} />
              <img src={logoLight} alt="" className={`${styles.logo} ${styles.logoLight}`} />
            </Link>
            <p className={styles.tagline}>
              The open platform where AI agents do the work across your business — connected
              to every system, under your control.
            </p>
            <p className={styles.bopLine}>
              An open-source project by the{' '}
              <a
                href="https://www.best-odoo-partners.com/"
                target="_blank"
                rel="noopener noreferrer"
                className={styles.bopLink}>
                BOP Alliance
              </a>
              .
            </p>
          </div>

          <div className={styles.columns}>
            {columns.map((column) => (
              <div key={column.title} className={styles.column}>
                <div className={styles.columnTitle}>{column.title}</div>
                <ul className={styles.linkList}>
                  {column.links.map((link) => (
                    <li key={link.label}>
                      <FooterLinkItem link={link} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        <div className={styles.bottomBar}>
          <p className={styles.copyright}>© {year} oc8</p>
        </div>
      </div>
    </footer>
  );
}
