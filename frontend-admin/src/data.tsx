import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import { getGroups } from "./auth";
import { AUTH_ENABLED } from "./config";
import type { ReminderSettings, ScheduleSettings, User } from "./types";

type Data = {
  users: User[];
  userById: (id: string) => User | undefined;
  usersLoaded: boolean;
  loadError: boolean;
  reloadUsers: () => Promise<void>;
  upsertUser: (u: User) => void;
  removeUser: (id: string) => void;

  reminders: ReminderSettings | null;
  schedule: ScheduleSettings | null;
  reloadSettings: () => Promise<void>;
  timezone: string;

  isAdmin: boolean;
  isClinician: boolean; // clinician or admin: may read and write patient notes
};

const Ctx = createContext<Data | null>(null);

export function DataProvider({ children }: { children: ReactNode }) {
  const [users, setUsers] = useState<User[]>([]);
  const [usersLoaded, setUsersLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [reminders, setReminders] = useState<ReminderSettings | null>(null);
  const [schedule, setSchedule] = useState<ScheduleSettings | null>(null);
  // Local mode has no sign-in: the local backend treats every request as an admin.
  const [groups, setGroups] = useState<string[]>(AUTH_ENABLED ? [] : ["admin"]);

  const reloadUsers = useCallback(async () => {
    try {
      setUsers((await api.listAllUsers()).sort((a, b) => a.name.localeCompare(b.name)));
      setLoadError(false);
    } catch {
      setLoadError(true);
    } finally {
      setUsersLoaded(true);
    }
  }, []);

  const reloadSettings = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.getReminderSettings(), api.getSchedule()]);
      setReminders(r);
      setSchedule(s);
    } catch {
      setLoadError(true);
    }
  }, []);

  useEffect(() => {
    void reloadUsers();
    void reloadSettings();
    if (AUTH_ENABLED) void getGroups().then(setGroups);
  }, [reloadUsers, reloadSettings]);

  const value = useMemo<Data>(() => {
    const byId = new Map(users.map((u) => [u.user_id, u]));
    return {
      users,
      userById: (id) => byId.get(id),
      usersLoaded,
      loadError,
      reloadUsers,
      upsertUser: (u) =>
        setUsers((prev) => [...prev.filter((x) => x.user_id !== u.user_id), u].sort((a, b) => a.name.localeCompare(b.name))),
      removeUser: (id) => setUsers((prev) => prev.filter((u) => u.user_id !== id)),
      reminders,
      schedule,
      reloadSettings,
      timezone: reminders?.timezone ?? "UTC",
      isAdmin: groups.includes("admin"),
      isClinician: groups.includes("admin") || groups.includes("clinician"),
    };
  }, [users, usersLoaded, loadError, reminders, schedule, groups, reloadUsers, reloadSettings]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useData(): Data {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useData outside DataProvider");
  return ctx;
}
