import { betterAuth } from "better-auth";
import { prismaAdapter } from "better-auth/adapters/prisma";
import { PrismaClient } from "../generated/prisma";
import { nextCookies } from "better-auth/next-js";

const prisma = new PrismaClient();
const disableSignUp = ["1", "true", "yes"].includes(
  (process.env.DISABLE_SIGN_UP ?? "").toLowerCase()
);

export const auth = betterAuth({
  database: prismaAdapter(prisma, {
    provider: "postgresql",
  }),
  user: {
    additionalFields: {
      is_admin: {
        type: "boolean",
        input: false,
      },
    },
  },
  trustedOrigins: ["http://localhost:3000", "http://sp.localhost:3000"],
  emailAndPassword: {
    enabled: true,
    disableSignUp,
  },
  plugins: [
    nextCookies(), // Enable Next.js cookie handling
  ],
});

export type Session = typeof auth.$Infer.Session;
