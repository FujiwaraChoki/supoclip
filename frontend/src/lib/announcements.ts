export type Announcement = {
  id: string;
  title: string;
  description: string;
  publishedAt: string;
  href: string;
  action: string;
};

export type ManagedAnnouncement = Announcement & { isPublished: boolean };

export const ANNOUNCEMENTS_UPDATED_EVENT = "supoclip:announcements-updated";
