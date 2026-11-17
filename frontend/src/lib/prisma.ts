import { PrismaClient } from "../generated/prisma";
import { databaseUrlWithPoolLimits } from "./database-url";

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined;
};

const prisma = globalForPrisma.prisma ?? new PrismaClient({
  datasourceUrl: databaseUrlWithPoolLimits(process.env.DATABASE_URL),
});

if (process.env.NODE_ENV !== "production") globalForPrisma.prisma = prisma;

export default prisma;
