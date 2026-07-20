import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { HOSTED_APP_URL, getSiteUrl } from "@/lib/blog-posts";

const LAST_UPDATED = "July 2, 2026";
const CONTACT_EMAIL = "support@supoclip.com";

export const metadata: Metadata = {
  title: "Terms of Service | SupoClip",
  description:
    "The terms that govern your use of the SupoClip apps and hosted video-clipping service, including subscriptions purchased on the web or through the App Store.",
  alternates: {
    canonical: `${getSiteUrl()}/terms`,
  },
  openGraph: {
    title: "SupoClip Terms of Service",
    description: "The terms that govern your use of SupoClip.",
    type: "website",
    url: `${getSiteUrl()}/terms`,
    siteName: "SupoClip",
  },
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2
        className="text-2xl font-bold tracking-tight"
        style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
      >
        {title}
      </h2>
      <div className="space-y-3 text-base leading-7 text-muted-foreground">{children}</div>
    </section>
  );
}

export default function TermsOfServicePage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="border-b bg-background/95">
        <div className="mx-auto flex h-16 max-w-3xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Image src="/logo.png" alt="SupoClip" width={24} height={24} className="rounded-lg" />
            <span
              className="text-lg font-bold tracking-tight"
              style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
            >
              SupoClip
            </span>
          </Link>
          <a href={HOSTED_APP_URL} target="_blank" rel="noopener noreferrer">
            <Button variant="ghost" size="sm" className="hidden sm:inline-flex">
              Hosted App
              <ExternalLink className="h-3.5 w-3.5" />
            </Button>
          </a>
        </div>
      </header>

      <article className="mx-auto max-w-3xl px-6 py-14 md:py-20">
        <div className="space-y-3">
          <h1
            className="text-4xl font-extrabold tracking-tight sm:text-5xl"
            style={{ fontFamily: "var(--font-syne), var(--font-geist-sans), system-ui" }}
          >
            Terms of Service
          </h1>
          <p className="text-sm text-muted-foreground">Last updated: {LAST_UPDATED}</p>
        </div>

        <div className="mt-12 space-y-10">
          <Section title="Agreement">
            <p>
              These Terms of Service (&ldquo;Terms&rdquo;) govern your use of the SupoClip apps and
              hosted service (the &ldquo;Service&rdquo;), which turn long-form videos into short,
              captioned clips. By creating an account or using the Service you agree to these Terms.
              If you do not agree, do not use the Service.
            </p>
            <p>
              SupoClip is also available as open-source software you can self-host. These Terms
              apply to the SupoClip-operated hosted service; self-hosted instances are governed by
              the applicable open-source license and the operator&rsquo;s own terms.
            </p>
          </Section>

          <Section title="Your Account">
            <p>
              You must provide accurate information when creating an account and keep your
              credentials secure. You are responsible for activity that occurs under your account.
              You must be at least 13 years old (or the minimum age required in your jurisdiction)
              to use the Service.
            </p>
          </Section>

          <Section title="Your Content &amp; Responsibilities">
            <p>
              You retain ownership of the videos you submit and the clips the Service generates from
              them. You grant us the limited rights needed to process, store, and deliver that
              content to you as part of operating the Service.
            </p>
            <p>
              You are solely responsible for having the necessary rights to any content you submit,
              including videos referenced by URL. You may not use the Service to process content
              that infringes the rights of others, is unlawful, or violates a platform&rsquo;s terms
              that bind you.
            </p>
          </Section>

          <Section title="Subscriptions &amp; Billing">
            <p>
              Paid plans (for example Pro and Scale) unlock higher monthly clip-generation limits.
              The same plan entitlement applies to your account regardless of where you purchased
              it.
            </p>
            <p className="font-medium text-foreground">Purchases on the web.</p>
            <p>
              Web subscriptions are billed by our payment processor, Stripe, on a monthly basis.
              They renew automatically until canceled. You can cancel anytime via the billing
              portal in Settings; access continues until the end of the paid period.
            </p>
            <p className="font-medium text-foreground">Purchases in the iOS app.</p>
            <p>
              Subscriptions purchased in the iOS app are billed to your Apple ID as auto-renewable
              subscriptions. Payment is charged at confirmation of purchase, and the subscription
              renews automatically unless canceled at least 24 hours before the end of the current
              period. You can manage or cancel App Store subscriptions in your device&rsquo;s
              subscription settings; they cannot be managed from the web. Refunds for App Store
              purchases are handled by Apple.
            </p>
            <p>
              A subscription is managed by the platform where it was purchased. Prices may change
              with notice; changes apply at the next renewal. Except where required by law or by
              the platform&rsquo;s refund policy, fees are non-refundable.
            </p>
          </Section>

          <Section title="Acceptable Use">
            <ul className="list-disc space-y-2 pl-6">
              <li>No infringing, unlawful, or harmful content.</li>
              <li>No attempts to probe, disrupt, or overload the Service or circumvent usage limits.</li>
              <li>No reselling or white-labeling the hosted Service without our written permission.</li>
              <li>No use of the Service to violate third-party platform terms that apply to you.</li>
            </ul>
            <p>We may suspend or terminate accounts that violate these Terms.</p>
          </Section>

          <Section title="Intellectual Property">
            <p>
              The Service, including its software, design, and branding, is owned by SupoClip or its
              licensors. The open-source components are licensed under their respective licenses.
              These Terms do not grant you any rights to our trademarks.
            </p>
          </Section>

          <Section title="Disclaimers">
            <p>
              The Service is provided &ldquo;as is&rdquo; and &ldquo;as available.&rdquo;
              AI-generated output (transcripts, captions, clip selections, virality estimates) may
              contain errors and is provided without warranty of accuracy. To the fullest extent
              permitted by law, we disclaim all warranties, express or implied, including fitness
              for a particular purpose and non-infringement.
            </p>
          </Section>

          <Section title="Limitation of Liability">
            <p>
              To the fullest extent permitted by law, SupoClip will not be liable for indirect,
              incidental, special, consequential, or punitive damages, or for lost profits, data, or
              goodwill. Our aggregate liability for claims relating to the Service is limited to the
              amount you paid us in the twelve months before the claim arose.
            </p>
          </Section>

          <Section title="Termination">
            <p>
              You may stop using the Service and delete your account at any time. We may suspend or
              terminate your access for violation of these Terms or where required by law. Sections
              that by their nature should survive termination (such as ownership, disclaimers, and
              limitation of liability) survive.
            </p>
          </Section>

          <Section title="Changes to These Terms">
            <p>
              We may update these Terms from time to time. When we do, we will revise the
              &ldquo;Last updated&rdquo; date above and, where appropriate, provide additional
              notice. Your continued use of the Service after an update means you accept the
              revised Terms.
            </p>
          </Section>

          <Section title="Contact Us">
            <p>
              Questions about these Terms? Email us at{" "}
              <a className="text-foreground underline" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
            </p>
          </Section>
        </div>

        <div className="mt-14 border-t pt-8 text-sm text-muted-foreground">
          <Link href="/privacy" className="underline hover:text-foreground">
            Privacy Policy
          </Link>
          <span className="mx-2">&middot;</span>
          <Link href="/" className="underline hover:text-foreground">
            Back to SupoClip
          </Link>
        </div>
      </article>
    </main>
  );
}
