import prisma from "@/lib/prisma";

export function getPrismaClient() {
  return prisma;
}
