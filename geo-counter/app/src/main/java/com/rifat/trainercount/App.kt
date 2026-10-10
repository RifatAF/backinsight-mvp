package com.rifat.trainercount

import android.app.Application
import com.rifat.trainercount.data.Store
import com.rifat.trainercount.geo.GeoSync
import com.rifat.trainercount.geo.Notifier

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Store.init(this)
        Notifier.channels(this)
        GeoSync.schedule(this)
    }
}
