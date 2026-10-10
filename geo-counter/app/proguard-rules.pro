# Приёмники и воркер создаются системой по имени из манифеста, R8 сохраняет их сам.
-keep class com.rifat.trainercount.geo.RefreshWorker { <init>(...); }
